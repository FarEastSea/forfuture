"""采集引擎：拉取 → 归一化 → 落库 → 媒体 → 索引触发。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.fingerprint import fingerprint_for_key
from app.browser.humanize import human_click, human_scroll, pause
from app.browser.pool import browser_pool
from app.core.config import settings
from app.core.errors import AntiBotError, CredentialExpiredError, CredentialUnavailableError, RateLimitedError
from app.core.events import EventType, event_bus
from app.core.logging import get_logger
from app.core.settings_store import settings_store
from app.crawl.persist import persist_item
from app.crawl.ratelimit import rate_limiter
from app.crawl.risk import mark_expired, mark_failure, mark_verified, pick_credential
from app.domain.enums import CrawlMode, JobStatus, Platform
from app.domain.identity import BrowserProfile, LoginCredential, PlatformAccount
from app.domain.ops import CrawlJob, CrawlJobEvent
from app.knowledge.indexer import index_item
from app.platforms.registry import get_platform, load_builtin_platforms
from app.platforms.xhs.auth import XHSAuthenticator
from app.platforms.xhs.client import XHSFetcher

logger = get_logger("app.crawl")


class CrawlEngine:
    async def run_job(self, session: AsyncSession, job: CrawlJob) -> None:
        load_builtin_platforms()
        job.status = JobStatus.RUNNING.value
        job.started_at = datetime.now()
        job.heartbeat_at = job.started_at
        await session.flush()
        await event_bus.publish(EventType.CRAWL_JOB_STARTED, {"id": job.id, "platform": job.platform})

        try:
            stats = await self._execute(session, job)
            job.stats = stats
            job.status = JobStatus.SUCCEEDED.value
        except CredentialUnavailableError as exc:
            job.status = JobStatus.FAILED.value
            job.error = str(exc)
            await self._event(session, job, "error", str(exc))
        except Exception as exc:
            logger.exception("采集任务失败 job=%s", job.id)
            job.status = JobStatus.FAILED.value
            job.error = str(exc)
            await self._event(session, job, "error", str(exc))
        finally:
            job.finished_at = datetime.now()
            job.heartbeat_at = job.finished_at
            await session.flush()
            await event_bus.publish(
                EventType.CRAWL_JOB_FINISHED,
                {"id": job.id, "platform": job.platform, "status": job.status, "stats": job.stats, "error": job.error},
            )

    async def _execute(self, session: AsyncSession, job: CrawlJob) -> dict[str, Any]:
        plugin = get_platform(job.platform)
        cfg = await settings_store.load_all(session)
        allow_degraded = not bool(cfg.get("skip_crawl_when_credential_degraded", True))
        credential = await pick_credential(
            session, job.platform, preferred_id=job.credential_id, allow_degraded=allow_degraded
        )
        if credential is None:
            raise CredentialUnavailableError(f"{job.platform} 没有可用登录凭据")
        job.credential_id = credential.id
        credential.last_used_at = datetime.now()

        targets = await self._targets(session, job)
        if job.platform == Platform.QQ.value:
            return await self._run_qq(session, job, plugin, credential, targets, cfg)
        return await self._run_xhs(session, job, plugin, credential, targets, cfg)

    async def _run_qq(
        self,
        session: AsyncSession,
        job: CrawlJob,
        plugin: Any,
        credential: LoginCredential,
        targets: list[PlatformAccount],
        cfg: dict[str, Any],
    ) -> dict[str, Any]:
        stats = {"items_new": 0, "items_updated": 0, "comments": 0, "targets": 0}
        mode = CrawlMode(job.mode)
        proxy = credential.proxy.url if credential.proxy else None
        fetcher = plugin.make_fetcher(credential.cookies, proxy=proxy)
        try:
            for index, target in enumerate(targets):
                created, updated, comments = await self._crawl_qq_target(
                    session, job, plugin, fetcher, credential, target, mode, cfg
                )
                stats["items_new"] += created
                stats["items_updated"] += updated
                stats["comments"] += comments
                stats["targets"] += 1
                job.progress = {
                    "stage": "feed",
                    "current": index + 1,
                    "total": len(targets),
                    "message": target.account_id,
                }
                await event_bus.publish(EventType.CRAWL_JOB_PROGRESS, {"id": job.id, **job.progress})
                await session.flush()
            await mark_verified(session, credential)
        except CredentialExpiredError as exc:
            await mark_expired(session, credential, reason=str(exc))
            raise
        except RateLimitedError as exc:
            rate_limiter.from_error(f"qq:{credential.id}", exc)
            await mark_failure(session, credential, reason=str(exc))
            raise
        except Exception as exc:
            await mark_failure(session, credential, reason=str(exc))
            raise
        finally:
            await fetcher.close()
        return stats

    async def _run_xhs(
        self,
        session: AsyncSession,
        job: CrawlJob,
        plugin: Any,
        credential: LoginCredential,
        targets: list[PlatformAccount],
        cfg: dict[str, Any],
    ) -> dict[str, Any]:
        stats = {"items_new": 0, "items_updated": 0, "comments": 0, "targets": 0}
        mode = CrawlMode(job.mode)
        profile = await self._ensure_xhs_profile(session, credential)
        proxy = credential.proxy.url if credential.proxy else None
        try:
            async with browser_pool.session(
                credential_key=f"xhs:{credential.id}",
                profile_dir=profile.profile_dir,
                fingerprint=profile.fingerprint,
                proxy=proxy,
                storage_state=credential.storage_state if not _profile_has_session(profile.profile_dir) else None,
            ) as context:
                page = context.pages[0] if context.pages else await context.new_page()
                await self._inject_xhs_cookies(context, credential)
                await page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded", timeout=45_000)
                await pause(1.4, 0.35)
                await human_scroll(page, 360)
                probe = await XHSAuthenticator(page).probe(credential.cookies or [])
                if not probe.ok:
                    if probe.detail == "credential-expired":
                        raise CredentialExpiredError("小红书登录态失效")
                    raise AntiBotError(f"小红书登录探测未通过: {probe.detail}")
                if probe.platform_uid:
                    credential.platform_uid = probe.platform_uid
                if probe.nickname:
                    credential.nickname = probe.nickname
                fetcher = XHSFetcher(page)
                for index, target in enumerate(targets):
                    created, updated, comments = await self._crawl_xhs_target(
                        session, job, plugin, fetcher, page, credential, target, mode, cfg
                    )
                    stats["items_new"] += created
                    stats["items_updated"] += updated
                    stats["comments"] += comments
                    stats["targets"] += 1
                    job.progress = {
                        "stage": "feed",
                        "current": index + 1,
                        "total": len(targets),
                        "message": target.account_id,
                    }
                    await event_bus.publish(EventType.CRAWL_JOB_PROGRESS, {"id": job.id, **job.progress})
                    await session.flush()
            await mark_verified(session, credential)
        except CredentialExpiredError as exc:
            await mark_expired(session, credential, reason=str(exc))
            raise
        except RateLimitedError as exc:
            rate_limiter.from_error(f"xhs:{credential.id}", exc)
            await mark_failure(session, credential, reason=str(exc))
            raise
        except Exception as exc:
            await mark_failure(session, credential, reason=str(exc))
            raise
        return stats

    async def _crawl_qq_target(
        self,
        session: AsyncSession,
        job: CrawlJob,
        plugin: Any,
        fetcher: Any,
        credential: LoginCredential,
        target: PlatformAccount,
        mode: CrawlMode,
        cfg: dict[str, Any],
    ) -> tuple[int, int, int]:
        created = updated = comments_n = 0
        empty_streak = 0
        max_pages = int(cfg.get("qq_max_pages") or 150)
        delay = float(cfg.get("qq_page_delay_seconds") or 0.8)
        jitter = float(cfg.get("crawl_jitter_ratio") or 0.35)
        checkpoint = (job.checkpoint or {}).get(str(target.id), {})
        pos = int(checkpoint.get("pos") or 0)

        for page_no in range(max_pages):
            await rate_limiter.wait(f"qq:{credential.id}", delay, jitter)
            raw = await fetcher.fetch_feed(target.account_id, cursor=str(pos))
            page = plugin.parser.parse_feed(raw, target_uid=target.account_id)
            if not page.items:
                empty_streak += 1
                if empty_streak >= 2:
                    break
            else:
                empty_streak = 0
            for item in page.items:
                extra = []
                expected = int(item.metrics.get("comment") or 0)
                if item.platform_item_id and expected > len(item.comments):
                    extra = await self._fetch_qq_comments(
                        plugin, fetcher, target.account_id, item.platform_item_id
                    )
                    if extra:
                        item.comments.extend(extra)
                row, is_new = await persist_item(session, item, target=target, mode=mode)
                await self._index_quietly(session, row)
                created += int(is_new)
                updated += int(not is_new)
                comments_n += len(item.comments)
            pos += 40
            job.checkpoint = {**(job.checkpoint or {}), str(target.id): {"pos": pos, "page": page_no}}
            job.heartbeat_at = datetime.now()
            if page.exhausted:
                break
            await pause(delay, jitter)

        target.last_crawled_at = datetime.now()
        target.last_success_at = target.last_crawled_at
        return created, updated, comments_n

    async def _crawl_xhs_target(
        self,
        session: AsyncSession,
        job: CrawlJob,
        plugin: Any,
        fetcher: XHSFetcher,
        page: Any,
        credential: LoginCredential,
        target: PlatformAccount,
        mode: CrawlMode,
        cfg: dict[str, Any],
    ) -> tuple[int, int, int]:
        created = updated = comments_n = 0
        max_pages = int(cfg.get("xhs_max_pages") or 80)
        delay = float(cfg.get("xhs_page_delay_seconds") or 1.2)
        jitter = float(cfg.get("crawl_jitter_ratio") or 0.35)
        uid = target.platform_uid or target.account_id
        checkpoint = (job.checkpoint or {}).get(str(target.id), {})
        cursor = checkpoint.get("cursor") or ""

        for page_no in range(max_pages):
            await rate_limiter.wait(f"xhs:{credential.id}", delay, jitter)
            raw = await fetcher.fetch_feed(uid, cursor=cursor or None)
            feed = plugin.parser.parse_feed(raw, target_uid=uid)
            if not feed.items and feed.exhausted:
                break
            for summary in feed.items:
                detail_raw = await fetcher.fetch_item(summary.platform_item_id)
                item = plugin.parser.parse_item_detail(detail_raw) or summary
                expected = int(item.metrics.get("comment") or 0)
                if expected > len(item.comments):
                    extra = await self._fetch_xhs_comments(
                        plugin, fetcher, page, item.platform_item_id
                    )
                    item.comments.extend(extra)
                row, is_new = await persist_item(session, item, target=target, mode=mode)
                await self._index_quietly(session, row)
                created += int(is_new)
                updated += int(not is_new)
                comments_n += len(item.comments)
                await pause(0.45, 0.4)
            cursor = feed.next_cursor or ""
            job.checkpoint = {
                **(job.checkpoint or {}),
                str(target.id): {"cursor": cursor, "page": page_no},
            }
            job.heartbeat_at = datetime.now()
            if feed.exhausted or not cursor:
                break
            await pause(delay, jitter)

        target.last_crawled_at = datetime.now()
        target.last_success_at = target.last_crawled_at
        return created, updated, comments_n

    async def _fetch_qq_comments(self, plugin, fetcher, uin: str, tid: str):
        from app.platforms.qq import constants as C

        collected = []
        for page in range(C.MAX_COMMENT_PAGES):
            raw = await fetcher.fetch_comments(tid, cursor=str(page * C.COMMENT_PAGE_SIZE), uin=uin)
            comments, _, exhausted = plugin.parser.parse_comments(raw)
            collected.extend(comments)
            if exhausted:
                break
            await pause(0.25, 0.2)
        return collected

    async def _fetch_xhs_comments(self, plugin, fetcher: XHSFetcher, page: Any, note_id: str):
        collected = []
        cursor = None
        unlocked = False
        for _ in range(30):
            try:
                raw = await fetcher.fetch_comments(note_id, cursor=cursor)
            except AntiBotError as exc:
                if not unlocked and "461" in str(exc):
                    await self._unlock_xhs_comments(page, note_id)
                    unlocked = True
                    raw = await fetcher.fetch_comments(note_id, cursor=cursor)
                else:
                    logger.warning("小红书评论拉取中止 note=%s: %s", note_id, exc)
                    break
            comments, next_cursor, exhausted = plugin.parser.parse_comments(raw)
            collected.extend(comments)
            cursor = next_cursor
            if exhausted or not cursor:
                break
            await pause(0.35, 0.3)
        return collected

    async def _unlock_xhs_comments(self, page: Any, note_id: str) -> None:
        """461 常见于未发生真实鼠标交互。打开笔记页点一次评论入口。"""
        try:
            await page.goto(
                f"https://www.xiaohongshu.com/explore/{note_id}",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            await pause(0.8, 0.3)
            await human_scroll(page, 280)
            for selector in ("text=评论", ".chat-wrapper", "[class*='engage-bar']"):
                loc = page.locator(selector).first
                if await loc.count() == 0:
                    continue
                handle = await loc.element_handle()
                if handle is not None:
                    await human_click(page, handle)
                    await pause(0.4, 0.3)
                    break
        except Exception as exc:
            logger.warning("小红书评论解锁点击失败 note=%s: %s", note_id, exc)

    async def _ensure_xhs_profile(self, session: AsyncSession, credential: LoginCredential) -> BrowserProfile:
        if credential.browser_profile is not None:
            return credential.browser_profile
        name = f"xhs:{credential.id}"
        existing = (
            await session.execute(select(BrowserProfile).where(BrowserProfile.name == name))
        ).scalar_one_or_none()
        if existing is not None:
            credential.browser_profile_id = existing.id
            await session.flush()
            return existing

        root = settings.browser_profile_path
        rescued = root / "xhs_login"
        dedicated = root / f"xhs_{credential.id}"
        if rescued.exists() and not dedicated.exists():
            profile_dir = str(rescued)
        else:
            dedicated.mkdir(parents=True, exist_ok=True)
            profile_dir = str(dedicated)

        row = BrowserProfile(
            name=name,
            platform=Platform.XHS.value,
            fingerprint=fingerprint_for_key(name),
            profile_dir=profile_dir,
            last_used_at=datetime.now(),
        )
        session.add(row)
        await session.flush()
        credential.browser_profile_id = row.id
        return row

    async def _inject_xhs_cookies(self, context: Any, credential: LoginCredential) -> None:
        cookies = []
        for item in credential.cookies or []:
            name = item.get("name")
            if not name:
                continue
            cookie = {
                "name": str(name),
                "value": str(item.get("value") or ""),
                "domain": item.get("domain") or ".xiaohongshu.com",
                "path": item.get("path") or "/",
            }
            cookies.append(cookie)
        if cookies:
            try:
                await context.add_cookies(cookies)
            except Exception as exc:
                logger.warning("写入小红书 Cookie 失败: %s", exc)

    async def _index_quietly(self, session: AsyncSession, row: Any) -> None:
        try:
            await index_item(session, row)
        except Exception as exc:
            logger.warning("知识库索引失败 item=%s: %s", getattr(row, "id", None), exc)

    async def _targets(self, session: AsyncSession, job: CrawlJob) -> list[PlatformAccount]:
        stmt = select(PlatformAccount).where(
            PlatformAccount.platform == job.platform, PlatformAccount.is_enabled.is_(True)
        )
        if job.target_account_ids:
            stmt = stmt.where(PlatformAccount.id.in_(job.target_account_ids))
        return list((await session.execute(stmt)).scalars().all())

    async def _event(self, session: AsyncSession, job: CrawlJob, level: str, message: str) -> None:
        session.add(
            CrawlJobEvent(job_id=job.id, level=level, message=message, created_at=datetime.now())
        )


def _profile_has_session(profile_dir: str) -> bool:
    from pathlib import Path

    path = Path(profile_dir)
    if not path.exists():
        return False
    return any(path.rglob("Cookies")) or (path / "Default").exists()


engine = CrawlEngine()

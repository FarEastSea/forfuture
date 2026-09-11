"""把 legacy 表的数据搬进统一内容模型。

设计要点：
  - 幂等：靠 migration_audit 表和新表的唯一约束，重复执行不会产生重复行，可以在切换前
    随时重跑以追平增量。
  - 只读 legacy：全程不 UPDATE/DELETE 任何 legacy 表。
  - 媒体文件零改动：local_path 原样照抄，磁盘上的文件不移动、不改名、不删除。

用法（必须在 backend 目录下执行）：
    python scripts/migrate_legacy_data.py --dry-run
    python scripts/migrate_legacy_data.py
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.db import session_factory  # noqa: E402
from app.domain.content import ContentComment, ContentItem, ContentRevision  # noqa: E402
from app.domain.enums import (  # noqa: E402
    ContentType,
    CredentialStatus,
    MediaKind,
    MediaRole,
    MediaStatus,
    Platform,
)
from app.domain.identity import (  # noqa: E402
    AccountProfileHistory,
    LoginCredential,
    PlatformAccount,
)
from app.domain.media import ContentMedia, MediaAsset  # noqa: E402
from app.domain.ops import AppSetting, LLMProvider, MigrationAudit  # noqa: E402


# --------------------------------------------------------------------------- 工具


def parse_json(value: Any, default: Any) -> Any:
    """legacy 的 JSON 列有时是 str 有时已经是 dict/list，统一处理。"""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return default
    return default


def compute_content_hash(title: str | None, body: str | None, media_paths: list[str]) -> str:
    payload = json.dumps(
        {"title": title or "", "body": body or "", "media": sorted(media_paths)},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_local_static(value: str | None) -> bool:
    return bool(value) and str(value).startswith("/static/")


class Stats:
    def __init__(self) -> None:
        self.counters: dict[str, int] = defaultdict(int)

    def bump(self, key: str, amount: int = 1) -> None:
        self.counters[key] += amount

    def as_dict(self) -> dict[str, int]:
        return dict(sorted(self.counters.items()))


# --------------------------------------------------------------------------- 媒体


class MediaRegistry:
    """按 local_path / remote_url 去重地登记媒体资产。"""

    def __init__(self, session: AsyncSession, stats: Stats) -> None:
        self.session = session
        self.stats = stats
        self._by_local: dict[str, MediaAsset] = {}
        self._by_remote: dict[tuple[str, str], MediaAsset] = {}

    async def preload(self) -> None:
        for asset in (await self.session.execute(select(MediaAsset))).scalars():
            if asset.local_path:
                self._by_local[asset.local_path] = asset
            if asset.remote_url:
                self._by_remote[(asset.kind, asset.remote_url)] = asset

    async def ensure(
        self,
        *,
        kind: MediaKind,
        platform: str | None,
        local_path: str | None,
        remote_url: str | None,
        static_root: Path,
    ) -> MediaAsset | None:
        if not local_path and not remote_url:
            return None

        if local_path and local_path in self._by_local:
            return self._by_local[local_path]
        if not local_path and remote_url:
            existing = self._by_remote.get((kind.value, remote_url))
            if existing is not None:
                return existing

        status = MediaStatus.REMOTE_ONLY
        byte_size: int | None = None
        if is_local_static(local_path):
            # 只读磁盘做状态判定，绝不触碰文件本身
            disk_path = static_root / str(local_path)[len("/static/") :]
            if disk_path.is_file():
                status = MediaStatus.DOWNLOADED
                byte_size = disk_path.stat().st_size
                self.stats.bump("media_files_present")
            else:
                status = MediaStatus.MISSING
                self.stats.bump("media_files_missing")

        asset = MediaAsset(
            kind=kind.value,
            platform=platform,
            remote_url=remote_url,
            local_path=local_path if is_local_static(local_path) else None,
            byte_size=byte_size,
            status=status.value,
            fallback_urls=[],
            extra={},
        )
        self.session.add(asset)
        await self.session.flush()

        if asset.local_path:
            self._by_local[asset.local_path] = asset
        if asset.remote_url:
            self._by_remote[(asset.kind, asset.remote_url)] = asset
        self.stats.bump("media_assets_created")
        return asset


# --------------------------------------------------------------------------- 搬运


class LegacyMigrator:
    def __init__(self, session: AsyncSession, *, dry_run: bool) -> None:
        self.session = session
        self.dry_run = dry_run
        self.stats = Stats()
        self.static_root = settings.static_path
        self.media = MediaRegistry(session, self.stats)
        self.now = datetime.now()
        self._audit: set[tuple[str, int, str]] = set()
        self._target_by_key: dict[tuple[str, str], PlatformAccount] = {}

    # -- 审计 ---------------------------------------------------------------

    async def preload_audit(self) -> None:
        for row in (await self.session.execute(select(MigrationAudit))).scalars():
            self._audit.add((row.legacy_table, row.legacy_id, row.new_table))

    def already_migrated(self, legacy_table: str, legacy_id: int, new_table: str) -> bool:
        return (legacy_table, legacy_id, new_table) in self._audit

    def record(self, legacy_table: str, legacy_id: int, new_table: str, new_id: int) -> None:
        key = (legacy_table, legacy_id, new_table)
        if key in self._audit:
            return
        self._audit.add(key)
        self.session.add(
            MigrationAudit(
                legacy_table=legacy_table,
                legacy_id=legacy_id,
                new_table=new_table,
                new_id=new_id,
                migrated_at=self.now,
            )
        )

    async def legacy_rows(self, table: str) -> list[dict[str, Any]]:
        exists = (
            await self.session.execute(
                text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table}"}
            )
        ).scalar()
        if not exists:
            return []
        result = await self.session.execute(text(f"SELECT * FROM {table} ORDER BY id"))
        return [dict(row) for row in result.mappings()]

    # -- accounts -> platform_accounts / login_credentials -------------------

    async def migrate_accounts(self) -> None:
        rows = await self.legacy_rows("accounts")
        for row in rows:
            if int(row.get("is_target") or 0) == 1:
                await self._migrate_target_account(row)
            else:
                await self._migrate_login_credential(row)

        # 采集到的作者可能不在 accounts 里，后面按需补建目标账号
        for account in (await self.session.execute(select(PlatformAccount))).scalars():
            self._target_by_key[(account.platform, account.account_id)] = account

    async def _migrate_target_account(self, row: dict[str, Any]) -> None:
        platform = str(row.get("platform") or "").strip()
        account_id = str(row.get("account_id") or "").strip()
        if not platform or not account_id:
            self.stats.bump("accounts_skipped_invalid")
            return

        existing = (
            await self.session.execute(
                select(PlatformAccount).where(
                    PlatformAccount.platform == platform,
                    PlatformAccount.account_id == account_id,
                )
            )
        ).scalar_one_or_none()

        avatar_url = row.get("avatar_url")
        asset = await self.media.ensure(
            kind=MediaKind.AVATAR,
            platform=platform,
            local_path=avatar_url if is_local_static(avatar_url) else None,
            remote_url=None if is_local_static(avatar_url) else avatar_url,
            static_root=self.static_root,
        )

        if existing is None:
            existing = PlatformAccount(
                platform=platform,
                account_id=account_id,
                platform_uid=row.get("platform_uid"),
                nickname=row.get("nickname"),
                signature=row.get("signature"),
                avatar_media_id=asset.id if asset else None,
                avatar_url=avatar_url,
                is_enabled=True,
                extra={"legacy_status": row.get("status")},
            )
            self.session.add(existing)
            await self.session.flush()
            self.stats.bump("targets_created")
        else:
            self.stats.bump("targets_existing")

        self._target_by_key[(platform, account_id)] = existing
        self.record("accounts", int(row["id"]), "platform_accounts", existing.id)
        await self._migrate_profile_history(row, existing)

    async def _migrate_profile_history(
        self, row: dict[str, Any], account: PlatformAccount
    ) -> None:
        if self.already_migrated("accounts", int(row["id"]), "account_profile_history"):
            return

        entries: list[AccountProfileHistory] = []
        for item in parse_json(row.get("nickname_history"), []):
            if not isinstance(item, dict):
                continue
            entries.append(
                AccountProfileHistory(
                    account_id=account.id,
                    field="nickname",
                    old_value=None,
                    new_value=item.get("nickname"),
                    observed_at=_coerce_datetime(item.get("time")) or self.now,
                )
            )
        for item in parse_json(row.get("avatar_history"), []):
            value = item.get("avatar_url") if isinstance(item, dict) else item
            observed = item.get("time") if isinstance(item, dict) else None
            entries.append(
                AccountProfileHistory(
                    account_id=account.id,
                    field="avatar",
                    old_value=None,
                    new_value=value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                    observed_at=_coerce_datetime(observed) or self.now,
                )
            )

        for entry in entries:
            self.session.add(entry)
        if entries:
            await self.session.flush()
            self.stats.bump("profile_history_rows", len(entries))
        self.record("accounts", int(row["id"]), "account_profile_history", account.id)

    async def _migrate_login_credential(self, row: dict[str, Any]) -> None:
        platform = str(row.get("platform") or "").strip()
        label = str(row.get("account_id") or "").strip()
        if not platform or not label:
            self.stats.bump("credentials_skipped_invalid")
            return

        # 登录账号的头像同样登记成媒体资产。放在存在性判断之前，保证重跑时能给
        # 早先建好的凭据补上遗漏的头像（例如历史上从未下载成功的那条）。
        avatar_value = row.get("avatar_url")
        avatar = await self.media.ensure(
            kind=MediaKind.AVATAR,
            platform=platform,
            local_path=avatar_value if is_local_static(avatar_value) else None,
            remote_url=None if is_local_static(avatar_value) else avatar_value,
            static_root=self.static_root,
        )

        existing = (
            await self.session.execute(
                select(LoginCredential).where(
                    LoginCredential.platform == platform,
                    LoginCredential.account_label == label,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if avatar is not None and not (existing.extra or {}).get("avatar_media_id"):
                existing.extra = {**(existing.extra or {}), "avatar_media_id": avatar.id}
            self.stats.bump("credentials_existing")
            self.record("accounts", int(row["id"]), "login_credentials", existing.id)
            return

        credential = LoginCredential(
            platform=platform,
            account_label=label,
            platform_uid=row.get("platform_uid"),
            nickname=row.get("nickname"),
            cookies=parse_json(row.get("cookies"), []),
            storage_state=parse_json(row.get("token"), None) if row.get("token") else None,
            token=row.get("token"),
            status=_map_credential_status(row.get("status")),
            is_enabled=True,
            failure_count=int(row.get("failure_count") or 0),
            last_failure_at=row.get("last_failure_at"),
            last_failure_reason=row.get("last_failure_reason"),
            cooldown_until=row.get("risk_cooldown_until"),
            last_validated_at=row.get("cookie_last_validated_at"),
            last_refreshed_at=row.get("last_cookie_refresh_at"),
            last_login_at=row.get("last_login"),
            extra={
                "legacy_avatar_url": avatar_value,
                "avatar_media_id": avatar.id if avatar else None,
            },
        )
        self.session.add(credential)
        await self.session.flush()
        self.stats.bump("credentials_created")
        self.record("accounts", int(row["id"]), "login_credentials", credential.id)

    # -- 目标账号兜底 --------------------------------------------------------

    async def ensure_target(self, platform: str, account_id: str) -> PlatformAccount | None:
        if not account_id:
            return None
        key = (platform, account_id)
        if key in self._target_by_key:
            return self._target_by_key[key]

        account = PlatformAccount(
            platform=platform,
            account_id=account_id,
            is_enabled=False,
            notes="迁移时根据历史内容自动补建（legacy accounts 表中没有对应记录）",
            extra={"auto_created_during_migration": True},
        )
        self.session.add(account)
        await self.session.flush()
        self._target_by_key[key] = account
        self.stats.bump("targets_auto_created")
        return account

    # -- qq_posts -> content_items ------------------------------------------

    async def migrate_qq_posts(self) -> None:
        rows = await self.legacy_rows("qq_posts")
        comments_by_post = await self._group_legacy_comments("qq_comments", "post_id")

        for row in rows:
            legacy_id = int(row["id"])
            if self.already_migrated("qq_posts", legacy_id, "content_items"):
                self.stats.bump("qq_posts_skipped_done")
                continue

            target = await self.ensure_target(Platform.QQ.value, str(row.get("qq_number") or ""))
            images = parse_json(row.get("images"), [])
            media_paths = [
                img.get("local_path") or img.get("url")
                for img in images
                if isinstance(img, dict) and (img.get("local_path") or img.get("url"))
            ]

            avatar = await self.media.ensure(
                kind=MediaKind.AVATAR,
                platform=Platform.QQ.value,
                local_path=row.get("author_avatar") if is_local_static(row.get("author_avatar")) else None,
                remote_url=None if is_local_static(row.get("author_avatar")) else row.get("author_avatar"),
                static_root=self.static_root,
            )

            extra: dict[str, Any] = {}
            if row.get("forward_content"):
                extra["forward_content"] = row["forward_content"]

            item = ContentItem(
                platform=Platform.QQ.value,
                platform_item_id=str(row.get("post_id")),
                content_type=ContentType.QQ_MOMENT.value,
                target_account_id=target.id if target else None,
                author_platform_uid=row.get("author_qq"),
                author_name=row.get("author_nickname"),
                author_avatar_media_id=avatar.id if avatar else None,
                title=None,
                body=row.get("content"),
                posted_at=row.get("post_time"),
                ip_location=None,
                geo_location=row.get("location"),
                device=row.get("device_info"),
                source_url=None,
                metrics={
                    "like": int(row.get("like_count") or 0),
                    "comment": int(row.get("comment_count") or 0),
                },
                extra=extra,
                raw=parse_json(row.get("raw_data"), None),
                content_hash=compute_content_hash(None, row.get("content"), [p for p in media_paths if p]),
                first_seen_at=row.get("crawled_at"),
                last_seen_at=row.get("updated_at") or row.get("crawled_at"),
            )
            self.session.add(item)
            await self.session.flush()
            self.stats.bump("qq_posts_migrated")
            self.record("qq_posts", legacy_id, "content_items", item.id)

            await self._attach_images(item, images, Platform.QQ.value, "qq_images")
            await self._attach_video(
                item, row.get("video_url"), row.get("local_video_path"), Platform.QQ.value
            )
            await self._attach_revisions(item, parse_json(row.get("edit_history"), []))
            await self._attach_comments(
                item, comments_by_post.get(legacy_id, []), Platform.QQ.value, "qq_comments"
            )

    # -- xhs_notes -> content_items -----------------------------------------

    async def migrate_xhs_notes(self) -> None:
        rows = await self.legacy_rows("xhs_notes")
        comments_by_note = await self._group_legacy_comments("xhs_comments", "note_id")

        for row in rows:
            legacy_id = int(row["id"])
            if self.already_migrated("xhs_notes", legacy_id, "content_items"):
                self.stats.bump("xhs_notes_skipped_done")
                continue

            target = await self.ensure_target(Platform.XHS.value, str(row.get("xhs_uid") or ""))
            images = parse_json(row.get("images"), [])
            media_paths = [
                img.get("local_path") or img.get("url")
                for img in images
                if isinstance(img, dict) and (img.get("local_path") or img.get("url"))
            ]

            avatar = await self.media.ensure(
                kind=MediaKind.AVATAR,
                platform=Platform.XHS.value,
                local_path=row.get("author_avatar") if is_local_static(row.get("author_avatar")) else None,
                remote_url=None if is_local_static(row.get("author_avatar")) else row.get("author_avatar"),
                static_root=self.static_root,
            )

            extra: dict[str, Any] = {"note_type": row.get("note_type")}
            tags = parse_json(row.get("tags"), [])
            if tags:
                extra["tags"] = tags
            at_users = parse_json(row.get("at_user_list"), [])
            if at_users:
                extra["at_users"] = at_users
            if row.get("video_duration"):
                extra["video_duration"] = row["video_duration"]

            item = ContentItem(
                platform=Platform.XHS.value,
                platform_item_id=str(row.get("note_id")),
                content_type=ContentType.XHS_NOTE.value,
                target_account_id=target.id if target else None,
                author_platform_uid=row.get("author_uid"),
                author_name=row.get("author_nickname"),
                author_avatar_media_id=avatar.id if avatar else None,
                title=row.get("title"),
                body=row.get("content"),
                posted_at=row.get("post_time"),
                edited_at=row.get("last_update_time"),
                ip_location=row.get("ip_location"),
                geo_location=row.get("location"),
                device=row.get("device_info"),
                source_url=row.get("note_url"),
                metrics={
                    "like": int(row.get("like_count") or 0),
                    "comment": int(row.get("comment_count") or 0),
                    "collect": int(row.get("collect_count") or 0),
                    "share": int(row.get("share_count") or 0),
                },
                extra=extra,
                raw=parse_json(row.get("raw_data"), None),
                content_hash=compute_content_hash(
                    row.get("title"), row.get("content"), [p for p in media_paths if p]
                ),
                first_seen_at=row.get("crawled_at"),
                last_seen_at=row.get("updated_at") or row.get("crawled_at"),
            )
            self.session.add(item)
            await self.session.flush()
            self.stats.bump("xhs_notes_migrated")
            self.record("xhs_notes", legacy_id, "content_items", item.id)

            await self._attach_images(item, images, Platform.XHS.value, "xhs_images")
            await self._attach_video(
                item, row.get("video_url"), row.get("local_video_path"), Platform.XHS.value
            )
            await self._attach_revisions(item, parse_json(row.get("edit_history"), []))
            await self._attach_comments(
                item, comments_by_note.get(legacy_id, []), Platform.XHS.value, "xhs_comments"
            )

    # -- 附属数据 ------------------------------------------------------------

    async def _attach_images(
        self, item: ContentItem, images: list[Any], platform: str, _subdir: str
    ) -> None:
        for position, entry in enumerate(images):
            if not isinstance(entry, dict):
                continue
            local_path = entry.get("local_path")
            remote_url = entry.get("url")
            asset = await self.media.ensure(
                kind=MediaKind.IMAGE,
                platform=platform,
                local_path=local_path,
                remote_url=remote_url,
                static_root=self.static_root,
            )
            if asset is None:
                continue
            self.session.add(
                ContentMedia(
                    content_item_id=item.id,
                    media_asset_id=asset.id,
                    role=MediaRole.IMAGE.value,
                    position=position,
                )
            )
            self.stats.bump("content_media_images")
        await self.session.flush()

    async def _attach_video(
        self, item: ContentItem, remote_url: str | None, local_path: str | None, platform: str
    ) -> None:
        if not remote_url and not local_path:
            return
        asset = await self.media.ensure(
            kind=MediaKind.VIDEO,
            platform=platform,
            local_path=local_path,
            remote_url=remote_url,
            static_root=self.static_root,
        )
        if asset is None:
            return
        self.session.add(
            ContentMedia(
                content_item_id=item.id,
                media_asset_id=asset.id,
                role=MediaRole.VIDEO.value,
                position=0,
            )
        )
        self.stats.bump("content_media_videos")
        await self.session.flush()

    async def _attach_revisions(self, item: ContentItem, history: list[Any]) -> None:
        for index, entry in enumerate(history):
            if not isinstance(entry, dict):
                continue
            self.session.add(
                ContentRevision(
                    content_item_id=item.id,
                    revision_index=index,
                    changed_fields=entry.get("changes") or entry,
                    observed_at=_coerce_datetime(entry.get("time")) or self.now,
                )
            )
            self.stats.bump("content_revisions")
        if history:
            await self.session.flush()

    async def _group_legacy_comments(
        self, table: str, parent_column: str
    ) -> dict[int, list[dict[str, Any]]]:
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in await self.legacy_rows(table):
            parent = row.get(parent_column)
            if parent is not None:
                grouped[int(parent)].append(row)
        return grouped

    async def _attach_comments(
        self, item: ContentItem, rows: list[dict[str, Any]], platform: str, legacy_table: str
    ) -> None:
        if not rows:
            return

        # 先建所有评论，再回填父子关系：legacy 的 reply_to_id 指向 legacy 主键
        legacy_to_new: dict[int, ContentComment] = {}
        for row in rows:
            avatar_value = row.get("author_avatar")
            avatar = await self.media.ensure(
                kind=MediaKind.AVATAR,
                platform=platform,
                local_path=avatar_value if is_local_static(avatar_value) else None,
                remote_url=None if is_local_static(avatar_value) else avatar_value,
                static_root=self.static_root,
            )
            comment = ContentComment(
                content_item_id=item.id,
                platform_comment_id=str(row.get("comment_id") or f"legacy-{row['id']}"),
                author_platform_uid=row.get("author_qq") or row.get("author_uid"),
                author_name=row.get("author_nickname"),
                author_avatar_media_id=avatar.id if avatar else None,
                body=row.get("content"),
                like_count=int(row.get("like_count") or 0),
                sub_comment_count=int(row.get("sub_comment_count") or 0),
                commented_at=row.get("comment_time"),
                ip_location=row.get("ip_location"),
                reply_to_name=row.get("target_nickname"),
                is_author_reply=bool(row.get("is_author")),
                created_at=row.get("created_at") or self.now,
            )
            self.session.add(comment)
            legacy_to_new[int(row["id"])] = comment
            self.stats.bump("comments_migrated")

        await self.session.flush()

        for row in rows:
            reply_to = row.get("reply_to_id")
            if reply_to is None:
                continue
            child = legacy_to_new.get(int(row["id"]))
            parent = legacy_to_new.get(int(reply_to))
            if child is not None and parent is not None:
                child.parent_id = parent.id

        for legacy_id, comment in legacy_to_new.items():
            self.record(legacy_table, legacy_id, "content_comments", comment.id)
        await self.session.flush()

    # -- 配置与 AI 供应商 ----------------------------------------------------

    async def migrate_settings(self) -> None:
        from app.core.settings_store import SPEC_BY_KEY

        # legacy system_configs 的键名到新键名的映射
        key_map = {
            "auto_crawl_enabled": "auto_crawl_enabled",
            "auto_crawl_interval": "auto_crawl_interval_minutes",
            "skip_crawl_when_login_degraded": "skip_crawl_when_credential_degraded",
            "cookie_failure_threshold": "credential_failure_threshold",
            "risk_cooldown_minutes": "credential_cooldown_minutes",
            "xhs_crawl_detail_delay_seconds": "xhs_detail_delay_seconds",
            "xhs_profile_scroll_delay_seconds": "xhs_scroll_delay_seconds",
            "ai_vision_enabled": "kb_vision_enabled",
            "allow_send_to_monitored": "allow_send_to_monitored",
            "admin_qq": "admin_qq",
            "admin_name": "admin_name",
            "login_notify_enabled": "login_notify_enabled",
            "server_base_url": "server_base_url",
        }

        existing_keys = {
            row.key for row in (await self.session.execute(select(AppSetting))).scalars()
        }

        for row in await self.legacy_rows("system_configs"):
            legacy_key = str(row.get("key") or "")
            new_key = key_map.get(legacy_key)
            if not new_key or new_key in existing_keys:
                continue
            spec = SPEC_BY_KEY.get(new_key)
            if spec is None:
                continue
            try:
                value = spec.coerce(row.get("value"))
            except Exception:
                self.stats.bump("settings_skipped_invalid")
                continue
            self.session.add(AppSetting(key=new_key, value=value, updated_at=self.now))
            existing_keys.add(new_key)
            self.stats.bump("settings_migrated")
        await self.session.flush()

    async def migrate_ai_configs(self) -> None:
        existing = {
            row.name for row in (await self.session.execute(select(LLMProvider))).scalars()
        }
        for row in await self.legacy_rows("ai_configs"):
            legacy_id = int(row["id"])
            name = str(row.get("name") or f"legacy-{legacy_id}")
            if name in existing or self.already_migrated("ai_configs", legacy_id, "llm_providers"):
                continue
            provider = LLMProvider(
                name=name,
                api_base=str(row.get("api_base") or ""),
                api_key=str(row.get("api_key") or ""),
                chat_model=str(row.get("model") or ""),
                embed_model=row.get("embed_model"),
                max_tokens=int(row.get("max_tokens") or 4096),
                temperature=float(row.get("temperature") or 0.7),
                is_active=bool(row.get("is_active")),
                extra={},
            )
            self.session.add(provider)
            await self.session.flush()
            existing.add(name)
            self.stats.bump("llm_providers_migrated")
            self.record("ai_configs", legacy_id, "llm_providers", provider.id)

    # -- 编排 ----------------------------------------------------------------

    async def run(self) -> dict[str, int]:
        await self.preload_audit()
        await self.media.preload()

        await self.migrate_accounts()
        await self.migrate_qq_posts()
        await self.migrate_xhs_notes()
        await self.migrate_settings()
        await self.migrate_ai_configs()

        if self.dry_run:
            await self.session.rollback()
            self.stats.bump("dry_run_rolled_back")
        else:
            await self.session.commit()
        return self.stats.as_dict()


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text_value[: len(fmt) + 2], fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text_value)
        except ValueError:
            return None
    return None


def _map_credential_status(legacy_status: Any) -> str:
    mapping = {
        "active": CredentialStatus.ACTIVE,
        "degraded": CredentialStatus.DEGRADED,
        "cooldown": CredentialStatus.COOLDOWN,
        "relogin_pending": CredentialStatus.RELOGIN_PENDING,
        "expired": CredentialStatus.EXPIRED,
        "disabled": CredentialStatus.DISABLED,
        "inactive": CredentialStatus.DISABLED,
    }
    return mapping.get(str(legacy_status or "").strip().lower(), CredentialStatus.ACTIVE).value


async def main_async(dry_run: bool) -> int:
    print("=== legacy 数据搬运 ===")
    print(f"模式: {'演练（结束回滚）' if dry_run else '实际写入'}")
    print(f"静态目录: {settings.static_path}")

    async with session_factory() as session:
        migrator = LegacyMigrator(session, dry_run=dry_run)
        stats = await migrator.run()

    print("\n--- 统计 ---")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="把 legacy 表数据搬进统一内容模型（幂等）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入，结束时回滚")
    args = parser.parse_args()
    return asyncio.run(main_async(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())

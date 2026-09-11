"""核对 legacy 表与统一内容模型是否一致。

检查项：
  1. 行数：每张 legacy 表的行数是否都在新模型里有对应。
  2. 字段抽样：随机抽若干条内容，逐字段比对正文/时间/计数/媒体数量。
  3. 媒体落地：新模型里每个 /static/ 路径在磁盘上是否真实存在。
  4. 媒体覆盖：legacy 引用过的每个 /static/ 路径是否都被新模型收录。

任何一项不通过都以非零退出码结束，可以直接接进部署前的校验流程。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.db import session_factory  # noqa: E402
from app.domain.content import ContentComment, ContentItem  # noqa: E402
from app.domain.enums import Platform  # noqa: E402
from app.domain.identity import LoginCredential, PlatformAccount  # noqa: E402
from app.domain.media import ContentMedia, MediaAsset  # noqa: E402

SAMPLE_SIZE = 25


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.failed = 0

    def add(self, name: str, ok: bool, detail: Any = None) -> None:
        self.checks.append({"check": name, "ok": ok, "detail": detail})
        if not ok:
            self.failed += 1

    def render(self) -> str:
        lines = []
        for entry in self.checks:
            mark = "PASS" if entry["ok"] else "FAIL"
            lines.append(f"[{mark}] {entry['check']}")
            if entry["detail"] is not None:
                rendered = json.dumps(entry["detail"], ensure_ascii=False, default=str)
                lines.append(f"       {rendered[:600]}")
        return "\n".join(lines)


async def legacy_count(session: AsyncSession, table: str) -> int | None:
    exists = (
        await session.execute(
            text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table}"}
        )
    ).scalar()
    if not exists:
        return None
    return int((await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())


async def check_row_counts(session: AsyncSession, report: Report) -> None:
    qq_posts = await legacy_count(session, "qq_posts")
    xhs_notes = await legacy_count(session, "xhs_notes")
    qq_comments = await legacy_count(session, "qq_comments")
    xhs_comments = await legacy_count(session, "xhs_comments")

    new_qq = int(
        (
            await session.execute(
                select(func.count()).select_from(ContentItem).where(ContentItem.platform == Platform.QQ.value)
            )
        ).scalar_one()
    )
    new_xhs = int(
        (
            await session.execute(
                select(func.count()).select_from(ContentItem).where(ContentItem.platform == Platform.XHS.value)
            )
        ).scalar_one()
    )
    new_comments = int(
        (await session.execute(select(func.count()).select_from(ContentComment))).scalar_one()
    )

    report.add(
        "QQ 说说行数一致",
        qq_posts is None or new_qq >= qq_posts,
        {"legacy": qq_posts, "new": new_qq},
    )
    report.add(
        "小红书笔记行数一致",
        xhs_notes is None or new_xhs >= xhs_notes,
        {"legacy": xhs_notes, "new": new_xhs},
    )
    legacy_comment_total = (qq_comments or 0) + (xhs_comments or 0)
    report.add(
        "评论行数一致",
        new_comments >= legacy_comment_total,
        {"legacy": legacy_comment_total, "new": new_comments},
    )

    legacy_accounts = await legacy_count(session, "accounts")
    new_targets = int(
        (await session.execute(select(func.count()).select_from(PlatformAccount))).scalar_one()
    )
    new_credentials = int(
        (await session.execute(select(func.count()).select_from(LoginCredential))).scalar_one()
    )
    report.add(
        "账号拆分完整",
        legacy_accounts is None or (new_targets + new_credentials) >= legacy_accounts,
        {"legacy": legacy_accounts, "targets": new_targets, "credentials": new_credentials},
    )


async def check_field_samples(session: AsyncSession, report: Report) -> None:
    mismatches: list[dict[str, Any]] = []

    if await legacy_count(session, "qq_posts"):
        rows = (
            await session.execute(
                text(
                    "SELECT post_id, content, post_time, like_count, comment_count, images, "
                    "local_video_path FROM qq_posts ORDER BY random() LIMIT :n"
                ),
                {"n": SAMPLE_SIZE},
            )
        ).mappings().all()
        for row in rows:
            mismatch = await _compare_item(
                session, Platform.QQ.value, row["post_id"], row, title_column=None
            )
            if mismatch:
                mismatches.append(mismatch)

    if await legacy_count(session, "xhs_notes"):
        rows = (
            await session.execute(
                text(
                    "SELECT note_id, title, content, post_time, like_count, comment_count, "
                    "images, local_video_path FROM xhs_notes ORDER BY random() LIMIT :n"
                ),
                {"n": SAMPLE_SIZE},
            )
        ).mappings().all()
        for row in rows:
            mismatch = await _compare_item(
                session, Platform.XHS.value, row["note_id"], row, title_column="title"
            )
            if mismatch:
                mismatches.append(mismatch)

    report.add("抽样字段一致", not mismatches, mismatches[:10] or None)


async def _compare_item(
    session: AsyncSession, platform: str, platform_item_id: str, legacy: Any, *, title_column: str | None
) -> dict[str, Any] | None:
    item = (
        await session.execute(
            select(ContentItem).where(
                ContentItem.platform == platform,
                ContentItem.platform_item_id == str(platform_item_id),
            )
        )
    ).scalar_one_or_none()
    if item is None:
        return {"platform": platform, "id": platform_item_id, "problem": "新模型中找不到对应内容"}

    problems: list[str] = []
    if (item.body or "") != (legacy["content"] or ""):
        problems.append("body 不一致")
    if title_column and (item.title or "") != (legacy[title_column] or ""):
        problems.append("title 不一致")
    if item.posted_at != legacy["post_time"]:
        problems.append(f"posted_at 不一致: {item.posted_at} vs {legacy['post_time']}")
    if int(item.metrics.get("like", 0)) != int(legacy["like_count"] or 0):
        problems.append("like 计数不一致")
    if int(item.metrics.get("comment", 0)) != int(legacy["comment_count"] or 0):
        problems.append("comment 计数不一致")

    legacy_images = legacy["images"]
    if isinstance(legacy_images, str):
        try:
            legacy_images = json.loads(legacy_images)
        except json.JSONDecodeError:
            legacy_images = []
    expected_images = len([i for i in (legacy_images or []) if isinstance(i, dict)])
    actual_images = int(
        (
            await session.execute(
                select(func.count())
                .select_from(ContentMedia)
                .where(ContentMedia.content_item_id == item.id, ContentMedia.role == "image")
            )
        ).scalar_one()
    )
    if actual_images != expected_images:
        problems.append(f"图片数量不一致: {actual_images} vs {expected_images}")

    if problems:
        return {"platform": platform, "id": platform_item_id, "problems": problems}
    return None


async def check_media_on_disk(session: AsyncSession, report: Report) -> None:
    static_root = settings.static_path
    rows = (
        (
            await session.execute(
                select(MediaAsset.id, MediaAsset.local_path, MediaAsset.status).where(
                    MediaAsset.local_path.isnot(None),
                    MediaAsset.status == "downloaded",
                )
            )
        )
        .all()
    )

    missing: list[str] = []
    for _asset_id, local_path, _status in rows:
        disk_path = static_root / str(local_path)[len("/static/") :]
        if not disk_path.is_file():
            missing.append(local_path)

    report.add(
        "媒体文件全部落地",
        not missing,
        {"total": len(rows), "missing": len(missing), "sample": missing[:15]} if missing else {"total": len(rows)},
    )


async def check_media_coverage(session: AsyncSession, report: Report) -> None:
    """legacy 引用过的每个本地路径都必须被新模型收录，否则就是搬运漏了。"""
    legacy_paths_sql = """
    SELECT DISTINCT p FROM (
        SELECT jsonb_array_elements(images::jsonb) ->> 'local_path' AS p
            FROM qq_posts WHERE images IS NOT NULL AND jsonb_typeof(images::jsonb) = 'array'
        UNION ALL SELECT local_video_path FROM qq_posts
        UNION ALL SELECT author_avatar FROM qq_posts
        UNION ALL SELECT jsonb_array_elements(images::jsonb) ->> 'local_path'
            FROM xhs_notes WHERE images IS NOT NULL AND jsonb_typeof(images::jsonb) = 'array'
        UNION ALL SELECT local_video_path FROM xhs_notes
        UNION ALL SELECT author_avatar FROM xhs_notes
        UNION ALL SELECT author_avatar FROM xhs_comments
        UNION ALL SELECT avatar_url FROM accounts
    ) t WHERE p IS NOT NULL AND p LIKE '/static/%'
    """
    if not await legacy_count(session, "qq_posts"):
        report.add("媒体引用覆盖", True, {"skipped": "legacy 表不存在"})
        return

    legacy_paths = {row[0] for row in (await session.execute(text(legacy_paths_sql))).all()}
    new_paths = {
        row[0]
        for row in (
            await session.execute(select(MediaAsset.local_path).where(MediaAsset.local_path.isnot(None)))
        ).all()
    }
    uncovered = sorted(legacy_paths - new_paths)
    report.add(
        "媒体引用覆盖",
        not uncovered,
        {"legacy": len(legacy_paths), "new": len(new_paths), "uncovered": len(uncovered), "sample": uncovered[:15]},
    )


async def main_async(strict: bool) -> int:
    report = Report()
    async with session_factory() as session:
        await check_row_counts(session, report)
        await check_field_samples(session, report)
        await check_media_on_disk(session, report)
        await check_media_coverage(session, report)

    print("=== 迁移校验 ===")
    print(report.render())
    print(f"\n结果: {len(report.checks) - report.failed}/{len(report.checks)} 项通过")

    if report.failed and strict:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 legacy 数据是否完整搬运到新模型")
    parser.add_argument(
        "--no-strict", action="store_true", help="即使有检查项不通过也返回 0（仅用于排查）"
    )
    args = parser.parse_args()
    return asyncio.run(main_async(strict=not args.no_strict))


if __name__ == "__main__":
    raise SystemExit(main())

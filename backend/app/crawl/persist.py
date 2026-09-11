"""把 NormalizedItem 落到统一内容模型。增量时更新计数并补空字段；覆盖时重下媒体。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.content import ContentComment, ContentItem, ContentRevision
from app.domain.enums import CrawlMode, MediaKind, MediaRole
from app.domain.identity import PlatformAccount
from app.domain.media import ContentMedia
from app.media.downloader import download_asset
from app.platforms.base import NormalizedComment, NormalizedItem


def content_hash(item: NormalizedItem) -> str:
    payload = json.dumps(
        {
            "title": item.title or "",
            "body": item.body or "",
            "media": sorted(m.url for m in item.media),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def persist_item(
    session: AsyncSession,
    item: NormalizedItem,
    *,
    target: PlatformAccount | None,
    mode: CrawlMode,
) -> tuple[ContentItem, bool]:
    now = datetime.now()
    existing = (
        await session.execute(
            select(ContentItem)
            .where(
                ContentItem.platform == item.platform.value,
                ContentItem.platform_item_id == item.platform_item_id,
            )
            .options(selectinload(ContentItem.media_links).selectinload(ContentMedia.media))
        )
    ).scalar_one_or_none()

    created = existing is None
    if existing is None:
        existing = ContentItem(
            platform=item.platform.value,
            platform_item_id=item.platform_item_id,
            content_type=item.content_type.value,
            first_seen_at=now,
        )
        session.add(existing)

    if existing.body and item.body and existing.body != item.body:
        session.add(
            ContentRevision(
                content_item_id=existing.id,
                revision_index=0 if created else 1,
                changed_fields={"body": {"old": existing.body, "new": item.body}},
                observed_at=now,
            )
        )

    existing.target_account_id = target.id if target else existing.target_account_id
    existing.author_platform_uid = item.author_uid or existing.author_platform_uid
    existing.author_name = item.author_name or existing.author_name
    existing.title = item.title if item.title is not None else existing.title
    existing.body = item.body if item.body is not None else existing.body
    existing.posted_at = item.posted_at or existing.posted_at
    existing.edited_at = item.edited_at or existing.edited_at
    existing.ip_location = item.ip_location or existing.ip_location
    existing.geo_location = item.geo_location or existing.geo_location
    existing.device = item.device or existing.device
    existing.source_url = item.source_url or existing.source_url
    existing.metrics = {**(existing.metrics or {}), **item.metrics}
    # 增量也要更新 forward_content，修掉旧实现只在新建时写入的缺陷
    existing.extra = {**(existing.extra or {}), **item.extra}
    existing.raw = item.raw or existing.raw
    existing.content_hash = content_hash(item)
    existing.last_seen_at = now

    await session.flush()
    overwrite = mode == CrawlMode.OVERWRITE or created
    await _sync_media(session, existing, item, overwrite=overwrite)
    await _sync_comments(session, existing, item.comments)
    return existing, created


async def _sync_media(
    session: AsyncSession, row: ContentItem, item: NormalizedItem, *, overwrite: bool
) -> None:
    # AsyncSession 禁止 ORM 属性隐式触发数据库 IO。即使调用方曾使用
    # selectinload，identity map 中已有/过期对象仍可能让 row.media_links
    # 退回懒加载并抛出 MissingGreenlet，因此这里始终显式查询一次。
    links = list(
        (
            await session.execute(
                select(ContentMedia)
                .where(ContentMedia.content_item_id == row.id)
                .options(selectinload(ContentMedia.media))
            )
        )
        .scalars()
        .all()
    )
    for position, media in enumerate(item.media):
        urls = [media.url, *media.fallback_urls]
        existing_link = next(
            (link for link in links if link.position == position and link.role == media.role),
            None,
        )
        existing_path = existing_link.media.local_path if existing_link and existing_link.media else None
        asset = await download_asset(
            session,
            platform=item.platform.value,
            kind=media.kind.value,
            urls=urls,
            existing_local_path=existing_path,
            overwrite=overwrite and media.kind != MediaKind.AVATAR,
        )
        if asset is None:
            continue
        already = next(
            (link for link in links if link.media_asset_id == asset.id and link.role == media.role),
            None,
        )
        if already is None:
            link = ContentMedia(
                content_item_id=row.id,
                media_asset_id=asset.id,
                role=media.role if media.role in {r.value for r in MediaRole} else media.kind.value,
                position=position,
            )
            session.add(link)
            links.append(link)
    await session.flush()


async def _sync_comments(
    session: AsyncSession, row: ContentItem, comments: list[NormalizedComment]
) -> None:
    if not comments:
        return
    existing = {
        c.platform_comment_id: c
        for c in (
            await session.execute(
                select(ContentComment).where(ContentComment.content_item_id == row.id)
            )
        ).scalars()
    }
    created: dict[str, ContentComment] = {}
    for comment in comments:
        current = existing.get(comment.platform_comment_id) or created.get(comment.platform_comment_id)
        if current is None:
            current = ContentComment(
                content_item_id=row.id,
                platform_comment_id=comment.platform_comment_id,
                created_at=datetime.now(),
            )
            session.add(current)
            created[comment.platform_comment_id] = current
        current.author_platform_uid = comment.author_uid
        current.author_name = comment.author_name
        current.body = comment.body
        current.like_count = comment.like_count
        current.sub_comment_count = comment.sub_comment_count
        current.commented_at = comment.commented_at
        current.ip_location = comment.ip_location
        current.reply_to_name = comment.reply_to_name
        current.is_author_reply = comment.is_author_reply
        current.raw = comment.raw
    await session.flush()
    all_comments = {**existing, **created}
    for comment in comments:
        child = all_comments.get(comment.platform_comment_id)
        if child and comment.parent_platform_id:
            parent = all_comments.get(comment.parent_platform_id)
            if parent:
                child.parent_id = parent.id
    await session.flush()

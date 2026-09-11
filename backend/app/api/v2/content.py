"""统一内容查询接口。QQ 说说与小红书笔记走同一套端点，用 platform 过滤。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminDep, PaginationDep, SessionDep
from app.core.errors import NotFoundError
from app.domain.content import ContentComment, ContentItem
from app.domain.enums import Platform
from app.domain.identity import PlatformAccount
from app.domain.media import ContentMedia, MediaAsset

router = APIRouter()


def _media_payload(link: ContentMedia) -> dict[str, Any]:
    asset = link.media
    return {
        "role": link.role,
        "position": link.position,
        "local_path": asset.local_path,
        "remote_url": asset.remote_url,
        "status": asset.status,
        "mime_type": asset.mime_type,
        "width": asset.width,
        "height": asset.height,
        "duration_seconds": asset.duration_seconds,
    }


def _item_payload(item: ContentItem, *, include_media: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": item.id,
        "platform": item.platform,
        "platform_item_id": item.platform_item_id,
        "content_type": item.content_type,
        "target_account_id": item.target_account_id,
        "author": {
            "platform_uid": item.author_platform_uid,
            "name": item.author_name,
            "avatar": None,
        },
        "title": item.title,
        "body": item.body,
        "posted_at": item.posted_at,
        "edited_at": item.edited_at,
        "ip_location": item.ip_location,
        "geo_location": item.geo_location,
        "device": item.device,
        "source_url": item.source_url,
        "metrics": item.metrics,
        "extra": item.extra,
        "first_seen_at": item.first_seen_at,
        "last_seen_at": item.last_seen_at,
    }
    if include_media:
        payload["media"] = [_media_payload(link) for link in item.media_links]
    return payload


@router.get("", summary="分页查询内容")
async def list_content(
    session: SessionDep,
    pagination: PaginationDep,
    _: AdminDep,
    platform: Platform | None = Query(None),
    target_account_id: int | None = Query(None),
    account_id: str | None = Query(None, description="监控目标的 QQ 号 / 小红书号"),
    author: str | None = Query(None, description="按作者昵称模糊匹配"),
    keyword: str | None = Query(None, description="标题/正文关键词"),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
) -> dict[str, Any]:
    conditions = []
    if platform:
        conditions.append(ContentItem.platform == platform.value)
    if target_account_id:
        conditions.append(ContentItem.target_account_id == target_account_id)
    if account_id:
        target_ids = select(PlatformAccount.id).where(PlatformAccount.account_id == account_id)
        if platform:
            target_ids = target_ids.where(PlatformAccount.platform == platform.value)
        conditions.append(
            ContentItem.target_account_id.in_(target_ids)
            | (ContentItem.author_platform_uid == account_id)
        )
    if author:
        conditions.append(ContentItem.author_name.ilike(f"%{author}%"))
    if keyword:
        pattern = f"%{keyword}%"
        conditions.append(ContentItem.body.ilike(pattern) | ContentItem.title.ilike(pattern))
    if since:
        conditions.append(ContentItem.posted_at >= since)
    if until:
        conditions.append(ContentItem.posted_at <= until)

    total_stmt = select(func.count()).select_from(ContentItem)
    if conditions:
        total_stmt = total_stmt.where(*conditions)
    total = int((await session.execute(total_stmt)).scalar_one())

    list_stmt = (
        select(ContentItem)
        .options(selectinload(ContentItem.media_links).joinedload(ContentMedia.media))
        .order_by(ContentItem.posted_at.desc().nullslast(), ContentItem.id.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    if conditions:
        list_stmt = list_stmt.where(*conditions)

    rows = (await session.execute(list_stmt)).scalars().unique().all()
    items = [_item_payload(item) for item in rows]
    await _attach_author_avatars(session, rows, items)
    await _attach_comment_previews(session, rows, items)
    return {
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "items": items,
    }


@router.get("/{item_id}", summary="内容详情（含评论树）")
async def get_content(item_id: int, session: SessionDep, _: AdminDep) -> dict[str, Any]:
    item = (
        (
            await session.execute(
                select(ContentItem)
                .where(ContentItem.id == item_id)
                .options(
                    selectinload(ContentItem.media_links).joinedload(ContentMedia.media),
                    selectinload(ContentItem.revisions),
                )
            )
        )
        .scalars()
        .unique()
        .one_or_none()
    )
    if item is None:
        raise NotFoundError(f"内容不存在: {item_id}")

    comments = (
        (
            await session.execute(
                select(ContentComment)
                .where(ContentComment.content_item_id == item_id)
                .order_by(ContentComment.commented_at.asc().nullslast(), ContentComment.id.asc())
            )
        )
        .scalars()
        .all()
    )

    avatar_ids = {c.author_avatar_media_id for c in comments if c.author_avatar_media_id}
    avatars: dict[int, str | None] = {}
    if avatar_ids:
        avatars = {
            row.id: row.local_path
            for row in (
                await session.execute(select(MediaAsset).where(MediaAsset.id.in_(avatar_ids)))
            )
            .scalars()
            .all()
        }

    nodes: dict[int, dict[str, Any]] = {}
    for comment in comments:
        nodes[comment.id] = {
            "id": comment.id,
            "platform_comment_id": comment.platform_comment_id,
            "parent_id": comment.parent_id,
            "author": {
                "platform_uid": comment.author_platform_uid,
                "name": comment.author_name,
                "avatar": avatars.get(comment.author_avatar_media_id or -1),
            },
            "body": comment.body,
            "like_count": comment.like_count,
            "sub_comment_count": comment.sub_comment_count,
            "commented_at": comment.commented_at,
            "ip_location": comment.ip_location,
            "reply_to_name": comment.reply_to_name,
            "is_author_reply": comment.is_author_reply,
            "replies": [],
        }

    roots: list[dict[str, Any]] = []
    for comment in comments:
        node = nodes[comment.id]
        parent = nodes.get(comment.parent_id) if comment.parent_id else None
        (parent["replies"] if parent else roots).append(node)

    payload = _item_payload(item)
    await _attach_author_avatars(session, [item], [payload])
    payload["comments"] = roots
    payload["comment_total"] = len(comments)
    payload["revisions"] = [
        {
            "revision_index": revision.revision_index,
            "changed_fields": revision.changed_fields,
            "observed_at": revision.observed_at,
        }
        for revision in sorted(item.revisions, key=lambda r: r.revision_index)
    ]
    return payload


async def _attach_author_avatars(session, rows: list[ContentItem], payloads: list[dict[str, Any]]) -> None:
    avatar_ids = {row.author_avatar_media_id for row in rows if row.author_avatar_media_id}
    if not avatar_ids:
        return
    avatars = {
        asset.id: asset.local_path or asset.remote_url
        for asset in (
            await session.execute(select(MediaAsset).where(MediaAsset.id.in_(avatar_ids)))
        ).scalars()
    }
    for row, payload in zip(rows, payloads):
        payload["author"]["avatar"] = avatars.get(row.author_avatar_media_id or -1)


async def _attach_comment_previews(session, rows: list[ContentItem], payloads: list[dict[str, Any]]) -> None:
    if not rows:
        return
    ids = [row.id for row in rows]
    comments = (
        (
            await session.execute(
                select(ContentComment)
                .where(ContentComment.content_item_id.in_(ids))
                .order_by(ContentComment.commented_at.asc().nullslast(), ContentComment.id.asc())
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[int, list[dict[str, Any]]] = {row.id: [] for row in rows}
    for comment in comments:
        grouped.setdefault(comment.content_item_id, []).append(
            {
                "id": comment.id,
                "author_nickname": comment.author_name,
                "content": comment.body,
                "comment_time": comment.commented_at,
                "like_count": comment.like_count,
                "ip_location": comment.ip_location,
                "is_author": comment.is_author_reply,
                "parent_id": comment.parent_id,
            }
        )
    by_id = {row.id: payload for row, payload in zip(rows, payloads)}
    for item_id, payload in by_id.items():
        payload["comments"] = grouped.get(item_id, [])[:40]
        payload["comment_total"] = len(grouped.get(item_id, []))

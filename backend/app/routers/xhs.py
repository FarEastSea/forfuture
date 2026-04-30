from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from typing import Optional
import os
from app.database import get_db
from app.config import settings
from app.models.xhs_post import XHSNote, XHSComment
from app.schemas import XHSCrawlRequest
from app.security import require_admin_http

router = APIRouter(dependencies=[Depends(require_admin_http)])


def _sanitize_avatar(avatar: str | None) -> str:
    if not avatar or not avatar.startswith("/static/"):
        return avatar or ""

    file_path = os.path.join(settings.static_dir, avatar[len("/static/"):])
    return avatar if os.path.isfile(file_path) else ""


def _sanitize_images(images: list | None) -> list:
    if not images:
        return []

    sanitized_images = []
    for image in images:
        if isinstance(image, dict):
            local_path = image.get("local_path")
            if local_path and local_path.startswith("/static/"):
                file_path = os.path.join(settings.static_dir, local_path[len("/static/"):])
                if not os.path.isfile(file_path):
                    image = {**image, "local_path": None}
        sanitized_images.append(image)

    return sanitized_images


def _sanitize_local_video_path(local_video_path: str | None) -> str | None:
    if not local_video_path or not local_video_path.startswith("/static/"):
        return local_video_path

    file_path = os.path.join(settings.static_dir, local_video_path[len("/static/"):])
    return local_video_path if os.path.isfile(file_path) else None


@router.get("/notes")
async def get_xhs_notes(
    xhs_uid: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    query = select(XHSNote).order_by(desc(XHSNote.post_time))
    if xhs_uid:
        query = query.where(XHSNote.xhs_uid == xhs_uid)
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    notes = result.scalars().all()

    note_list = []
    for note in notes:
        sanitized_images = _sanitize_images(note.images)
        sanitized_avatar = _sanitize_avatar(note.author_avatar)
        sanitized_local_video_path = _sanitize_local_video_path(note.local_video_path)

        comments_q = select(XHSComment).where(
            XHSComment.note_id == note.id, XHSComment.reply_to_id == None
        ).order_by(XHSComment.comment_time)
        top_comments = (await db.execute(comments_q)).scalars().all()

        replies_q = select(XHSComment).where(
            XHSComment.note_id == note.id, XHSComment.reply_to_id != None
        ).order_by(XHSComment.comment_time)
        all_replies = (await db.execute(replies_q)).scalars().all()
        replies_by_parent = {}
        for r in all_replies:
            replies_by_parent.setdefault(r.reply_to_id, []).append(r)

        def _fmt_comment(c, include_replies=True):
            d = {
                "id": c.id,
                "comment_id": c.comment_id,
                "author_uid": c.author_uid,
                "author_nickname": c.author_nickname,
                "author_avatar": c.author_avatar,
                "content": c.content,
                "like_count": c.like_count or 0,
                "target_nickname": c.target_nickname or "",
                "sub_comment_count": c.sub_comment_count or 0,
                "ip_location": c.ip_location or "",
                "is_author": c.is_author or 0,
                "comment_time": c.comment_time.isoformat() if c.comment_time else None,
            }
            if include_replies:
                d["replies"] = [_fmt_comment(r, False) for r in replies_by_parent.get(c.id, [])]
            return d

        note_list.append({
            "id": note.id,
            "xhs_uid": note.xhs_uid,
            "note_id": note.note_id,
            "note_type": note.note_type,
            "author_nickname": note.author_nickname,
            "author_avatar": sanitized_avatar,
            "title": note.title,
            "content": note.content,
            "images": sanitized_images,
            "video_url": note.video_url,
            "local_video_path": sanitized_local_video_path,
            "video_duration": note.video_duration,
            "tags": note.tags or [],
            "at_user_list": note.at_user_list or [],
            "like_count": note.like_count,
            "collect_count": note.collect_count,
            "comment_count": note.comment_count,
            "share_count": note.share_count,
            "ip_location": note.ip_location,
            "device_info": note.device_info,
            "location": note.location,
            "edit_history": note.edit_history or [],
            "post_time": note.post_time.isoformat() if note.post_time else None,
            "last_update_time": note.last_update_time.isoformat() if note.last_update_time else None,
            "updated_at": note.updated_at.isoformat() if note.updated_at else None,
            "note_url": note.note_url,
            "crawled_at": note.crawled_at.isoformat() if note.crawled_at else None,
            "comments": [_fmt_comment(c) for c in top_comments],
        })

    count_q = select(func.count(XHSNote.id))
    if xhs_uid:
        count_q = count_q.where(XHSNote.xhs_uid == xhs_uid)
    total = (await db.execute(count_q)).scalar() or 0

    return {"items": note_list, "total": total, "page": page, "page_size": page_size}


@router.get("/notes/{note_db_id}")
async def get_xhs_note_detail(note_db_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(XHSNote).where(XHSNote.id == note_db_id))
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    sanitized_images = _sanitize_images(note.images)
    sanitized_avatar = _sanitize_avatar(note.author_avatar)
    sanitized_local_video_path = _sanitize_local_video_path(note.local_video_path)

    comments_q = select(XHSComment).where(
        XHSComment.note_id == note.id, XHSComment.reply_to_id == None
    ).order_by(XHSComment.comment_time)
    top_comments = (await db.execute(comments_q)).scalars().all()

    replies_q = select(XHSComment).where(
        XHSComment.note_id == note.id, XHSComment.reply_to_id != None
    ).order_by(XHSComment.comment_time)
    all_replies = (await db.execute(replies_q)).scalars().all()
    replies_by_parent = {}
    for r in all_replies:
        replies_by_parent.setdefault(r.reply_to_id, []).append(r)

    def _fmt_comment(c, include_replies=True):
        d = {
            "id": c.id,
            "comment_id": c.comment_id,
            "author_uid": c.author_uid,
            "author_nickname": c.author_nickname,
            "author_avatar": c.author_avatar,
            "content": c.content,
            "like_count": c.like_count or 0,
            "target_nickname": c.target_nickname or "",
            "sub_comment_count": c.sub_comment_count or 0,
            "ip_location": c.ip_location or "",
            "is_author": c.is_author or 0,
            "comment_time": c.comment_time.isoformat() if c.comment_time else None,
        }
        if include_replies:
            d["replies"] = [_fmt_comment(r, False) for r in replies_by_parent.get(c.id, [])]
        return d

    return {
        "id": note.id,
        "xhs_uid": note.xhs_uid,
        "note_id": note.note_id,
        "note_type": note.note_type,
        "author_nickname": note.author_nickname,
        "author_avatar": sanitized_avatar,
        "title": note.title,
        "content": note.content,
        "images": sanitized_images,
        "video_url": note.video_url,
        "local_video_path": sanitized_local_video_path,
        "video_duration": note.video_duration,
        "tags": note.tags or [],
        "at_user_list": note.at_user_list or [],
        "like_count": note.like_count,
        "collect_count": note.collect_count,
        "comment_count": note.comment_count,
        "share_count": note.share_count,
        "ip_location": note.ip_location,
        "device_info": note.device_info,
        "location": note.location,
        "edit_history": note.edit_history or [],
        "post_time": note.post_time.isoformat() if note.post_time else None,
        "last_update_time": note.last_update_time.isoformat() if note.last_update_time else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
        "note_url": note.note_url,
        "raw_data": note.raw_data,
        "crawled_at": note.crawled_at.isoformat() if note.crawled_at else None,
        "comments": [_fmt_comment(c) for c in top_comments],
    }


@router.post("/crawl")
async def trigger_xhs_crawl(req: XHSCrawlRequest):
    from app.services.xhs_crawler import xhs_crawler
    task_id = await xhs_crawler.start_crawl(req.user_ids, mode=req.mode)
    return {"task_id": task_id, "message": "抓取任务已启动"}


@router.post("/notes/{note_db_id}/crawl-comments")
async def crawl_note_comments(note_db_id: int, db: AsyncSession = Depends(get_db)):
    """为单条笔记抓取评论"""
    result = await db.execute(select(XHSNote).where(XHSNote.id == note_db_id))
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="笔记不存在")

    from app.services.xhs_crawler import xhs_crawler
    import asyncio
    asyncio.create_task(xhs_crawler.crawl_single_note_comments(note.note_id))
    return {"message": f"评论抓取任务已启动: {note.note_id}"}


@router.get("/crawl/status")
async def get_xhs_crawl_status():
    from app.services.xhs_crawler import xhs_crawler
    return xhs_crawler.get_status()


@router.get("/accounts")
async def get_xhs_accounts(db: AsyncSession = Depends(get_db)):
    from app.models.account import Account
    result = await db.execute(
        select(Account).where(Account.platform == "xhs", Account.is_target == 1)
    )
    accounts = result.scalars().all()
    return [
        {"id": a.id, "account_id": a.account_id, "nickname": a.nickname, "avatar_url": a.avatar_url}
        for a in accounts
    ]

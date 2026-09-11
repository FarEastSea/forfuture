from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import Optional
import os
from legacy.database import get_db
from legacy.models.qq_post import QQPost, QQComment
from legacy.schemas import QQPostOut, CrawlRequest
from legacy.config import settings
from legacy.security import require_admin_http

router = APIRouter(dependencies=[Depends(require_admin_http)])


def _check_images(images: list) -> list:
    """校验图片的 local_path 是否真实存在，不存在则清空并由前端提示重新抓取。"""
    if not images:
        return images
    checked = []
    for img in images:
        if isinstance(img, dict):
            lp = img.get("local_path")
            if lp and lp.startswith("/static/"):
                # /static/qq_images/xxx.jpg → ./static/qq_images/xxx.jpg
                file_path = os.path.join(settings.static_dir, lp[len("/static/"):])
                if not os.path.isfile(file_path):
                    img = {**img, "local_path": None}
        checked.append(img)
    return checked


def _check_avatar(avatar: str | None, qq_number: str = "") -> str:
    """校验头像文件是否存在；已抓取内容不再回退远程头像。"""
    if not avatar or not avatar.startswith("/static/"):
        return ""
    file_path = os.path.join(settings.static_dir, avatar[len("/static/"):])
    if os.path.isfile(file_path):
        return avatar
    return ""


def _check_local_video_path(local_video_path: str | None) -> str | None:
    """校验本地视频是否仍存在；不存在时清空并由前端提示重新抓取。"""
    if not local_video_path or not local_video_path.startswith("/static/"):
        return local_video_path

    file_path = os.path.join(settings.static_dir, local_video_path[len("/static/"):])
    return local_video_path if os.path.isfile(file_path) else None


@router.get("/posts")
async def get_qq_posts(
    qq_number: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    query = select(QQPost).order_by(desc(QQPost.post_time))
    if qq_number:
        query = query.where(QQPost.qq_number == qq_number)
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    posts = result.scalars().all()

    # 加载评论
    post_list = []
    for post in posts:
        comments_q = select(QQComment).where(QQComment.post_id == post.id).order_by(QQComment.comment_time)
        comments_result = await db.execute(comments_q)
        comments = comments_result.scalars().all()
        post_dict = {
            "id": post.id,
            "qq_number": post.qq_number,
            "post_id": post.post_id,
            "author_qq": post.author_qq,
            "author_nickname": post.author_nickname,
            "author_avatar": _check_avatar(post.author_avatar, post.author_qq or ""),
            "content": post.content,
            "images": _check_images(post.images or []),
            "video_url": post.video_url,
            "local_video_path": _check_local_video_path(post.local_video_path),
            "post_time": post.post_time.isoformat() if post.post_time else None,
            "like_count": post.like_count,
            "comment_count": post.comment_count,
            "forward_content": post.forward_content,
            "device_info": post.device_info,
            "location": post.location,
            "edit_history": post.edit_history or [],
            "updated_at": post.updated_at.isoformat() if post.updated_at else None,
            "crawled_at": post.crawled_at.isoformat() if post.crawled_at else None,
            "comments": [
                {
                    "id": c.id,
                    "author_nickname": c.author_nickname,
                    "author_avatar": _check_avatar(c.author_avatar),
                    "content": c.content,
                    "comment_time": c.comment_time.isoformat() if c.comment_time else None,
                }
                for c in comments
            ],
        }
        post_list.append(post_dict)

    # 总数
    from sqlalchemy import func
    count_q = select(func.count(QQPost.id))
    if qq_number:
        count_q = count_q.where(QQPost.qq_number == qq_number)
    total = (await db.execute(count_q)).scalar() or 0

    return {"items": post_list, "total": total, "page": page, "page_size": page_size}


@router.get("/posts/{post_id}")
async def get_qq_post_detail(post_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(QQPost).where(QQPost.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="动态不存在")

    comments_q = select(QQComment).where(QQComment.post_id == post.id).order_by(QQComment.comment_time)
    comments = (await db.execute(comments_q)).scalars().all()

    return {
        "id": post.id,
        "qq_number": post.qq_number,
        "post_id": post.post_id,
        "author_qq": post.author_qq,
        "author_nickname": post.author_nickname,
        "author_avatar": _check_avatar(post.author_avatar, post.author_qq or ""),
        "content": post.content,
        "images": _check_images(post.images or []),
        "video_url": post.video_url,
        "local_video_path": _check_local_video_path(post.local_video_path),
        "post_time": post.post_time.isoformat() if post.post_time else None,
        "like_count": post.like_count,
        "comment_count": post.comment_count,
        "forward_content": post.forward_content,
        "device_info": post.device_info,
        "location": post.location,
        "edit_history": post.edit_history or [],
        "updated_at": post.updated_at.isoformat() if post.updated_at else None,
        "raw_data": post.raw_data,
        "crawled_at": post.crawled_at.isoformat() if post.crawled_at else None,
        "comments": [
            {
                "id": c.id,
                "author_nickname": c.author_nickname,
                "author_avatar": _check_avatar(c.author_avatar),
                "content": c.content,
                "comment_time": c.comment_time.isoformat() if c.comment_time else None,
            }
            for c in comments
        ],
    }


@router.post("/crawl")
async def trigger_qq_crawl(req: CrawlRequest):
    from legacy.services.qq_crawler import qq_crawler
    try:
        task_id = await qq_crawler.start_crawl(
            req.account_ids,
            mode=req.mode,
            login_account_id=req.login_account_id,
        )
    except RuntimeError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"task_id": task_id, "message": "抓取任务已启动"}


@router.get("/crawl/status")
async def get_crawl_status():
    from legacy.services.qq_crawler import qq_crawler
    return qq_crawler.get_status()


@router.get("/accounts")
async def get_qq_accounts(db: AsyncSession = Depends(get_db)):
    from legacy.models.account import Account
    result = await db.execute(
        select(Account).where(Account.platform == "qq", Account.is_target == 1)
    )
    accounts = result.scalars().all()
    return [
        {"id": a.id, "account_id": a.account_id, "nickname": a.nickname, "avatar_url": a.avatar_url}
        for a in accounts
    ]

"""白名单工具。不允许 LLM 写裸 SQL。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Awaitable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.core.settings_store import settings_store
from app.crawl.queue import enqueue_job
from app.domain.content import ContentItem
from app.domain.enums import Platform
from app.domain.identity import PlatformAccount
from app.domain.ops import CrawlJob
from app.knowledge.retriever import search as kb_search

ToolHandler = Callable[..., Awaitable[Any]]

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_records",
            "description": "在已采集的 QQ/小红书记录知识库中检索",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "platform": {"type": "string", "enum": ["qq", "xhs"]},
                    "author": {"type": "string"},
                    "top_k": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_record",
            "description": "按 ID 读取一条内容",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "integer"}},
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "aggregate_records",
            "description": "按平台/作者统计内容数量，不接受任意 SQL",
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "enum": ["qq", "xhs"]},
                    "group_by": {"type": "string", "enum": ["platform", "author"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_targets",
            "description": "列出监控目标账号",
            "parameters": {"type": "object", "properties": {"platform": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_crawl",
            "description": "触发一次采集任务",
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "enum": ["qq", "xhs"]},
                    "mode": {"type": "string", "enum": ["incremental", "overwrite", "repair"]},
                },
                "required": ["platform"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_crawl_status",
            "description": "查询采集任务状态",
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "integer"}},
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_notification",
            "description": "通过 NapCat 发送通知。发给被监控账号需要 allow_send_to_monitored",
            "parameters": {
                "type": "object",
                "properties": {
                    "qq": {"type": "string"},
                    "message": {"type": "string"},
                    "to_monitored": {"type": "boolean"},
                },
                "required": ["qq", "message"],
            },
        },
    },
]


async def dispatch(session: AsyncSession, name: str, arguments: dict[str, Any]) -> Any:
    handlers: dict[str, ToolHandler] = {
        "search_records": _search_records,
        "get_record": _get_record,
        "aggregate_records": _aggregate_records,
        "list_targets": _list_targets,
        "trigger_crawl": _trigger_crawl,
        "get_crawl_status": _get_crawl_status,
        "send_notification": _send_notification,
    }
    handler = handlers.get(name)
    if handler is None:
        raise ValidationError(f"未知工具: {name}")
    return await handler(session, **arguments)


async def _search_records(session: AsyncSession, query: str, platform: str | None = None, author: str | None = None, top_k: int = 8):
    return await kb_search(session, query, platform=platform, author=author, top_k=top_k)


async def _get_record(session: AsyncSession, id: int):
    item = await session.get(ContentItem, id)
    if item is None:
        return {"error": "not found"}
    return {
        "id": item.id,
        "platform": item.platform,
        "title": item.title,
        "body": item.body,
        "author_name": item.author_name,
        "posted_at": item.posted_at.isoformat() if item.posted_at else None,
        "metrics": item.metrics,
        "extra": item.extra,
    }


async def _aggregate_records(session: AsyncSession, platform: str | None = None, group_by: str = "platform"):
    if group_by == "author":
        stmt = select(ContentItem.author_name, func.count()).group_by(ContentItem.author_name)
    else:
        stmt = select(ContentItem.platform, func.count()).group_by(ContentItem.platform)
    if platform:
        stmt = stmt.where(ContentItem.platform == platform)
    rows = (await session.execute(stmt)).all()
    return [{"key": key, "count": count} for key, count in rows]


async def _list_targets(session: AsyncSession, platform: str | None = None):
    stmt = select(PlatformAccount)
    if platform:
        stmt = stmt.where(PlatformAccount.platform == platform)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {"id": r.id, "platform": r.platform, "account_id": r.account_id, "nickname": r.nickname}
        for r in rows
    ]


async def _trigger_crawl(session: AsyncSession, platform: str, mode: str = "incremental"):
    import asyncio

    from app.core.db import session_scope
    from app.crawl.engine import engine
    from app.domain.ops import CrawlJob

    job, queued = await enqueue_job(session, platform=Platform(platform), mode=mode, requested_by="agent")
    if not queued:
        job_id = job.id

        async def _bg() -> None:
            async with session_scope() as worker:
                fresh = await worker.get(CrawlJob, job_id)
                if fresh:
                    await engine.run_job(worker, fresh)

        asyncio.create_task(_bg())
    return {"job_id": job.id, "status": job.status}


async def _get_crawl_status(session: AsyncSession, job_id: int):
    job = await session.get(CrawlJob, job_id)
    if job is None:
        return {"error": "not found"}
    return {"id": job.id, "status": job.status, "progress": job.progress, "stats": job.stats, "error": job.error}


async def _send_notification(session: AsyncSession, qq: str, message: str, to_monitored: bool = False):
    cfg = await settings_store.load_all(session)
    if not qq or qq == "admin":
        qq = str(cfg.get("admin_qq") or "")
    if not qq:
        return {"error": "未配置管理员 QQ"}
    monitored = (
        await session.execute(
            select(PlatformAccount.id).where(
                PlatformAccount.platform == Platform.QQ.value,
                PlatformAccount.account_id == qq,
            )
        )
    ).scalar_one_or_none()
    if monitored is not None and not cfg.get("allow_send_to_monitored"):
        return {"error": "不允许向被监控账号发送通知"}
    try:
        from app.notifications.napcat import napcat_client

        sent = await napcat_client.send_message(qq, [{"type": "text", "content": message}])
        return {"ok": sent, "error": None if sent else "NapCat 未连接"}
    except Exception as exc:
        return {"error": str(exc)}

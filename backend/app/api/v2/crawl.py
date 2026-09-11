"""采集任务接口。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import AdminDep, PaginationDep, SessionDep
from app.core.db import session_scope
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.crawl.engine import engine
from app.crawl.queue import enqueue_job
from app.domain.enums import CrawlMode, JobStatus, Platform
from app.domain.ops import CrawlJob, CrawlJobEvent

logger = get_logger("app.api.crawl")
router = APIRouter()


class CrawlRequest(BaseModel):
    platform: Platform
    mode: CrawlMode = CrawlMode.INCREMENTAL
    target_account_ids: list[int] | None = None
    credential_id: int | None = None
    run_inline: bool = False


@router.post("", summary="创建采集任务")
async def create_job(payload: CrawlRequest, session: SessionDep, _: AdminDep):
    job, queued = await enqueue_job(
        session,
        platform=payload.platform,
        mode=payload.mode,
        target_account_ids=payload.target_account_ids,
        credential_id=payload.credential_id,
    )
    await session.commit()
    if payload.run_inline:
        async with session_scope() as worker_session:
            fresh = await worker_session.get(CrawlJob, job.id)
            if fresh:
                await engine.run_job(worker_session, fresh)
        await session.refresh(job)
    elif not queued:
        asyncio.create_task(_run_job_background(job.id))
    return _job_payload(job)


@router.get("", summary="采集任务列表")
async def list_jobs(
    session: SessionDep,
    pagination: PaginationDep,
    _: AdminDep,
    platform: Platform | None = Query(None),
):
    conditions = [CrawlJob.platform == platform.value] if platform else []
    total_stmt = select(func.count()).select_from(CrawlJob)
    if conditions:
        total_stmt = total_stmt.where(*conditions)
    total = int((await session.execute(total_stmt)).scalar_one())
    list_stmt = select(CrawlJob).order_by(CrawlJob.id.desc()).offset(pagination.offset).limit(pagination.limit)
    if conditions:
        list_stmt = list_stmt.where(*conditions)
    rows = (await session.execute(list_stmt)).scalars().all()
    return {"total": total, "items": [_job_payload(job) for job in rows]}


@router.get("/latest", summary="某平台最近一次采集任务")
async def latest_job(session: SessionDep, _: AdminDep, platform: Platform = Query(...)):
    job = (
        await session.execute(
            select(CrawlJob)
            .where(CrawlJob.platform == platform.value)
            .order_by(CrawlJob.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if job is None:
        return {"running": False, "status": "idle", "progress": None, "error": None, "job": None}
    events = (
        (
            await session.execute(
                select(CrawlJobEvent)
                .where(CrawlJobEvent.job_id == job.id)
                .order_by(CrawlJobEvent.id.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    running = job.status in {JobStatus.PENDING.value, JobStatus.RUNNING.value}
    progress = job.progress or {}
    message = progress.get("message") or job.status
    return {
        "running": running,
        "status": job.status,
        "progress": message,
        "error": job.error,
        "stats": job.stats,
        "job": _job_payload(job),
        "details": [
            {"time": e.created_at, "level": e.level, "msg": e.message}
            for e in reversed(list(events))
        ],
    }


@router.get("/{job_id}", summary="采集任务详情")
async def get_job(job_id: int, session: SessionDep, _: AdminDep):
    job = await session.get(CrawlJob, job_id)
    if job is None:
        raise NotFoundError(f"任务不存在: {job_id}")
    events = (
        (
            await session.execute(
                select(CrawlJobEvent)
                .where(CrawlJobEvent.job_id == job_id)
                .order_by(CrawlJobEvent.id)
            )
        )
        .scalars()
        .all()
    )
    payload = _job_payload(job)
    payload["events"] = [{"level": e.level, "message": e.message, "created_at": e.created_at} for e in events]
    return payload


async def _run_job_background(job_id: int) -> None:
    try:
        async with session_scope() as session:
            job = await session.get(CrawlJob, job_id)
            if job is None or job.status != JobStatus.PENDING.value:
                return
            await engine.run_job(session, job)
    except Exception:
        logger.exception("后台采集任务失败 job=%s", job_id)


def _job_payload(job: CrawlJob) -> dict:
    return {
        "id": job.id,
        "platform": job.platform,
        "mode": job.mode,
        "status": job.status,
        "progress": job.progress,
        "stats": job.stats,
        "error": job.error,
        "queued_at": job.queued_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }

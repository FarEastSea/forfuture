"""采集任务入队：Postgres 落盘 + 可选 Redis/arq。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EventType, event_bus
from app.domain.enums import CrawlMode, JobStatus, Platform
from app.domain.ops import CrawlJob


async def enqueue_job(
    session: AsyncSession,
    *,
    platform: Platform | str,
    mode: CrawlMode | str = CrawlMode.INCREMENTAL,
    target_account_ids: list[int] | None = None,
    credential_id: int | None = None,
    requested_by: str = "api",
) -> tuple[CrawlJob, bool]:
    job = CrawlJob(
        platform=platform.value if isinstance(platform, Platform) else platform,
        mode=mode.value if isinstance(mode, CrawlMode) else mode,
        status=JobStatus.PENDING.value,
        target_account_ids=target_account_ids,
        credential_id=credential_id,
        requested_by=requested_by,
        queued_at=datetime.now(),
        progress={},
        stats={},
        checkpoint={},
    )
    session.add(job)
    await session.flush()
    await event_bus.publish(
        EventType.CRAWL_JOB_QUEUED,
        {"id": job.id, "platform": job.platform, "mode": job.mode},
    )
    queued = False
    try:
        queued = await _try_arq(job.id)
    except Exception:
        queued = False
    return job, queued


async def _try_arq(job_id: int) -> bool:
    from app.core.config import settings

    if not settings.redis_enabled:
        return False
    try:
        from arq import create_pool
        from arq.connections import RedisSettings

        redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        await redis.enqueue_job("run_crawl_job", job_id)
        await redis.aclose()
        return True
    except Exception:
        return False

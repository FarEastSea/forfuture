"""arq worker 入口。Redis 不可用时，API 进程会在后台同步跑 engine.run_job。"""
from __future__ import annotations

from arq.connections import RedisSettings

from app.core.config import settings
from app.core.db import session_scope
from app.core.logging import setup_logging
from app.crawl.engine import engine
from app.domain.ops import CrawlJob


async def run_crawl_job(ctx, job_id: int) -> None:
    setup_logging()
    async with session_scope() as session:
        job = await session.get(CrawlJob, job_id)
        if job is None:
            return
        await engine.run_job(session, job)


class WorkerSettings:
    functions = [run_crawl_job]
    redis_settings = (
        RedisSettings.from_dsn(settings.redis_url) if settings.redis_enabled else RedisSettings()
    )
    max_jobs = 1

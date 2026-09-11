"""Agent-driven scheduled tasks."""
from __future__ import annotations

import asyncio

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.api.deps import AdminDep, SessionDep
from app.core.errors import NotFoundError, ValidationError
from app.domain.identity import PlatformAccount
from app.domain.ops import ScheduledTask, TaskRun
from app.scheduler import task_scheduler

router = APIRouter()


class TaskWrite(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    target_qq: str = Field(min_length=1, max_length=40)
    cron_expr: str | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=10080)
    message_format: str = "text"

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.cron_expr:
            try:
                CronTrigger.from_crontab(self.cron_expr.strip())
            except ValueError as exc:
                raise ValueError(f"无效的 Cron 表达式: {exc}") from exc
        if not self.cron_expr and self.interval_minutes is None:
            self.interval_minutes = 120
        return self


async def _ensure_safe_target(session, target_qq: str) -> None:
    monitored = (
        await session.execute(
            select(PlatformAccount.id).where(
                PlatformAccount.platform == "qq", PlatformAccount.account_id == target_qq
            )
        )
    ).scalar_one_or_none()
    if monitored is not None:
        from app.core.settings_store import settings_store

        config = await settings_store.load_all(session)
        if not config.get("allow_send_to_monitored", False):
            raise ValidationError("安全拦截：通知目标是被监控账号")


def _payload(task: ScheduledTask) -> dict:
    config = task.config or {}
    return {
        "id": task.id,
        "name": task.name,
        "description": task.description,
        "target_qq": config.get("target_qq") or "",
        "task_type": "recurring",
        "cron_expr": task.cron_expr,
        "interval_minutes": task.interval_seconds // 60 if task.interval_seconds else None,
        "message_format": config.get("message_format") or "text",
        "is_active": task.is_enabled,
        "last_run": task.last_run_at,
        "next_run": task.next_run_at,
        "run_count": task.run_count,
        "trigger_count": config.get("trigger_count", 0),
        "created_at": task.created_at,
    }


@router.get("")
async def list_tasks(session: SessionDep, _: AdminDep):
    rows = (await session.execute(select(ScheduledTask).order_by(ScheduledTask.id.desc()))).scalars().all()
    return {"items": [_payload(row) for row in rows]}


@router.post("")
async def create_task(payload: TaskWrite, session: SessionDep, _: AdminDep):
    await _ensure_safe_target(session, payload.target_qq)
    row = ScheduledTask(
        name=payload.name,
        description=payload.description,
        kind="agent",
        schedule_kind="cron" if payload.cron_expr else "interval",
        cron_expr=payload.cron_expr.strip() if payload.cron_expr else None,
        interval_seconds=None if payload.cron_expr else payload.interval_minutes * 60,
        config={"target_qq": payload.target_qq, "message_format": payload.message_format},
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    task_scheduler.register(row)
    await session.commit()
    return _payload(row)


@router.put("/{task_id}")
async def update_task(task_id: int, payload: TaskWrite, session: SessionDep, _: AdminDep):
    row = await session.get(ScheduledTask, task_id)
    if row is None:
        raise NotFoundError("任务不存在")
    await _ensure_safe_target(session, payload.target_qq)
    row.name = payload.name
    row.description = payload.description
    row.schedule_kind = "cron" if payload.cron_expr else "interval"
    row.cron_expr = payload.cron_expr.strip() if payload.cron_expr else None
    row.interval_seconds = None if payload.cron_expr else payload.interval_minutes * 60
    row.config = {**(row.config or {}), "target_qq": payload.target_qq, "message_format": payload.message_format}
    if row.is_enabled:
        task_scheduler.register(row)
    await session.commit()
    return _payload(row)


@router.delete("/{task_id}")
async def delete_task(task_id: int, session: SessionDep, _: AdminDep):
    row = await session.get(ScheduledTask, task_id)
    if row is None:
        raise NotFoundError("任务不存在")
    task_scheduler.remove(task_id)
    await session.delete(row)
    await session.commit()
    return {"ok": True}


@router.post("/{task_id}/toggle")
async def toggle_task(task_id: int, session: SessionDep, _: AdminDep):
    row = await session.get(ScheduledTask, task_id)
    if row is None:
        raise NotFoundError("任务不存在")
    row.is_enabled = not row.is_enabled
    if row.is_enabled:
        task_scheduler.register(row)
    else:
        task_scheduler.remove(row.id)
        row.next_run_at = None
    await session.commit()
    return {"is_active": row.is_enabled}


@router.post("/{task_id}/run-now")
async def run_now(task_id: int, session: SessionDep, _: AdminDep):
    if await session.get(ScheduledTask, task_id) is None:
        raise NotFoundError("任务不存在")
    asyncio.create_task(task_scheduler.execute(task_id))
    return {"ok": True}


@router.get("/{task_id}/logs")
async def task_logs(task_id: int, session: SessionDep, _: AdminDep):
    rows = (
        await session.execute(
            select(TaskRun).where(TaskRun.task_id == task_id).order_by(TaskRun.id.desc()).limit(100)
        )
    ).scalars().all()
    return {
        "items": [
            {
                "id": row.id,
                "run_at": row.started_at,
                "triggered": row.triggered,
                "ai_reason": row.output,
                "error": row.error,
                "status": row.status,
            }
            for row in rows
        ]
    }

"""Persistent APScheduler integration backed by scheduled_tasks/task_runs."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.agent.runtime import run_turn
from app.core.db import session_scope
from app.core.events import EventType, event_bus
from app.core.logging import get_logger
from app.domain.ops import AgentMessage, AgentSession, AgentToolCall, ScheduledTask, TaskRun

logger = get_logger("app.scheduler")


class TaskScheduler:
    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    async def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()
        async with session_scope() as session:
            tasks = (
                await session.execute(select(ScheduledTask).where(ScheduledTask.is_enabled.is_(True)))
            ).scalars().all()
            for task in tasks:
                self.register(task)
        logger.info("新任务调度器已启动，共注册 %s 个任务", len(tasks))

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def register(self, task: ScheduledTask) -> None:
        trigger = self._trigger(task)
        self.scheduler.add_job(
            self.execute,
            trigger=trigger,
            args=[task.id],
            id=self._job_id(task.id),
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
        job = self.scheduler.get_job(self._job_id(task.id))
        task.next_run_at = job.next_run_time.replace(tzinfo=None) if job and job.next_run_time else None

    def remove(self, task_id: int) -> None:
        job = self.scheduler.get_job(self._job_id(task_id))
        if job:
            self.scheduler.remove_job(job.id)

    async def execute(self, task_id: int) -> None:
        async with session_scope() as session:
            task = await session.get(ScheduledTask, task_id)
            if task is None or not task.is_enabled:
                return
            started = datetime.now()
            run = TaskRun(task_id=task.id, status="running", started_at=started)
            session.add(run)
            task.last_run_at = started
            task.run_count += 1
            await session.flush()
            await event_bus.publish(EventType.TASK_RUN_STARTED, {"task_id": task.id, "run_id": run.id})
            try:
                if task.kind == "agent":
                    output, triggered = await self._run_agent(session, task)
                else:
                    raise ValueError(f"不支持的任务类型: {task.kind}")
                run.status = "succeeded"
                run.output = output
                run.triggered = triggered
            except Exception as exc:
                run.status = "failed"
                run.error = str(exc)
                task.failure_count += 1
                logger.exception("定时任务执行失败 task=%s", task.id)
            finally:
                run.finished_at = datetime.now()
                job = self.scheduler.get_job(self._job_id(task.id))
                task.next_run_at = job.next_run_time.replace(tzinfo=None) if job and job.next_run_time else None
                await event_bus.publish(
                    EventType.TASK_RUN_FINISHED,
                    {"task_id": task.id, "run_id": run.id, "status": run.status, "triggered": run.triggered},
                )

    async def _run_agent(self, session: Any, task: ScheduledTask) -> tuple[str, bool]:
        config = task.config or {}
        target = str(config.get("target_qq") or "")
        prompt = (
            "执行一个定时监控任务。请用工具检索相关记录并判断条件是否满足；"
            "只有满足时才调用 send_notification，未满足时只说明原因。\n"
            f"任务：{task.description or task.name}\n"
            f"通知QQ：{target}\n"
            "禁止向被监控账号发送通知，除非系统设置明确允许。"
        )
        chat = AgentSession(title=f"定时任务：{task.name}")
        session.add(chat)
        await session.flush()
        chunks: list[str] = []
        async for event in run_turn(session, chat.id, prompt):
            if event.get("type") == "token" and event.get("content"):
                chunks.append(str(event["content"]))
        calls = (
            await session.execute(
                select(AgentToolCall)
                .join(AgentMessage, AgentMessage.id == AgentToolCall.message_id)
                .where(AgentMessage.session_id == chat.id, AgentToolCall.tool_name == "send_notification")
            )
        ).scalars().all()
        triggered = any(isinstance(call.result, dict) and call.result.get("ok") is True for call in calls)
        return "".join(chunks), triggered

    @staticmethod
    def _job_id(task_id: int) -> str:
        return f"scheduled_task_{task_id}"

    @staticmethod
    def _trigger(task: ScheduledTask):
        if task.schedule_kind == "cron" and task.cron_expr:
            return CronTrigger.from_crontab(task.cron_expr, timezone="Asia/Shanghai")
        if task.schedule_kind == "interval" and task.interval_seconds:
            return IntervalTrigger(seconds=task.interval_seconds, timezone="Asia/Shanghai")
        raise ValueError("任务缺少有效的 cron_expr 或 interval_seconds")


task_scheduler = TaskScheduler()

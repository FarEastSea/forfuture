"""运维域：应用设置、采集任务、调度、LLM 供应商、Agent 会话。

采集任务落库而不是放进程内存字典，这样重启不丢、多 worker 可见、前端可回溯历史。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.base import Base, TimestampMixin
from app.domain.enums import JobStatus


class AppSetting(Base):
    """运行期可调设置。值统一用 JSONB，读取侧按注册表做类型校验。"""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class CrawlJob(Base, TimestampMixin):
    __tablename__ = "crawl_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=JobStatus.PENDING.value
    )

    # 为空表示该平台下全部启用的目标
    target_account_ids: Mapped[list[int] | None] = mapped_column(JSONB)
    credential_id: Mapped[int | None] = mapped_column(
        ForeignKey("login_credentials.id", ondelete="SET NULL")
    )

    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    requested_by: Mapped[str | None] = mapped_column(String(60))

    # {"stage": "...", "current": n, "total": n, "message": "..."}
    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # {"items_new": n, "items_updated": n, "comments": n, "media_downloaded": n}
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # 断点续爬位置，按目标账号记录游标/页码
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)

    queued_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)

    events: Mapped[list["CrawlJobEvent"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        Index("ix_crawl_jobs_status_priority", "status", "priority", "id"),
        Index("ix_crawl_jobs_platform_created", "platform", "created_at"),
    )


class CrawlJobEvent(Base):
    __tablename__ = "crawl_job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("crawl_jobs.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    job: Mapped[CrawlJob] = relationship(back_populates="events")

    __table_args__ = (Index("ix_crawl_job_events_job_created", "job_id", "created_at"),)


class ScheduledTask(Base, TimestampMixin):
    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    schedule_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    cron_expr: Mapped[str | None] = mapped_column(String(120))
    interval_seconds: Mapped[int | None] = mapped_column(Integer)

    # 任务参数：采集任务放 platform/mode/targets；Agent 任务放 prompt/notify 目标
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    runs: Mapped[list["TaskRun"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )


class TaskRun(Base):
    __tablename__ = "task_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("scheduled_tasks.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    task: Mapped[ScheduledTask] = relationship(back_populates="runs")

    __table_args__ = (Index("ix_task_runs_task_started", "task_id", "started_at"),)


class LLMProvider(Base, TimestampMixin):
    __tablename__ = "llm_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    api_base: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    chat_model: Mapped[str] = mapped_column(String(200), nullable=False)
    embed_model: Mapped[str | None] = mapped_column(String(200))
    vision_model: Mapped[str | None] = mapped_column(String(200))
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=4096)
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class AgentSession(Base, TimestampMixin):
    __tablename__ = "agent_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="新对话")

    messages: Mapped[list["AgentMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    # 引用到的内容条目，供前端展示出处
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    session: Mapped[AgentSession] = relationship(back_populates="messages")
    tool_calls: Mapped[list["AgentToolCall"]] = relationship(
        back_populates="message", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (Index("ix_agent_messages_session_created", "session_id", "created_at"),)


class AgentToolCall(Base):
    """工具调用留痕，前端据此展示 Agent 的推理过程。"""

    __tablename__ = "agent_tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("agent_messages.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[str | None] = mapped_column(String(120))
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[Any] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    message: Mapped[AgentMessage] = relationship(back_populates="tool_calls")


class MigrationAudit(Base):
    """旧表 -> 新表的搬运留痕，保证迁移脚本幂等且可核对。"""

    __tablename__ = "migration_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    legacy_table: Mapped[str] = mapped_column(String(80), nullable=False)
    legacy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    new_table: Mapped[str] = mapped_column(String(80), nullable=False)
    new_id: Mapped[int] = mapped_column(Integer, nullable=False)
    migrated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "legacy_table", "legacy_id", "new_table", name="uq_migration_audit_legacy_new"
        ),
    )

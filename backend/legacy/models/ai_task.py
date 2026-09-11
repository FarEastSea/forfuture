from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, func, ForeignKey
from sqlalchemy.orm import relationship
from legacy.database import Base


class AITask(Base):
    __tablename__ = "ai_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    target_qq = Column(String(20), default="admin")  # "admin"=管理员QQ
    task_type = Column(String(20), default="recurring")  # temporal/recurring/oneoff
    cron_expr = Column(String(100))
    interval_minutes = Column(Integer)
    ai_suggested_interval = Column(Integer)  # AI建议的检查间隔（分钟）
    message_format = Column(String(50), default="text_image")
    is_active = Column(Boolean, default=True)
    last_run = Column(DateTime)
    last_triggered = Column(DateTime)
    last_checked_until = Column(DateTime)  # 时效性任务：已检查到的时间点
    run_count = Column(Integer, default=0)
    trigger_count = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())

    logs = relationship("TaskLog", back_populates="task", cascade="all, delete-orphan")


class TaskLog(Base):
    __tablename__ = "task_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("ai_tasks.id", ondelete="CASCADE"), nullable=False)
    run_at = Column(DateTime, server_default=func.now())
    triggered = Column(Boolean, default=False)
    ai_reason = Column(Text)
    message_sent = Column(Text)
    error = Column(Text)
    confirmed = Column(Boolean, default=False)  # 用户是否确认已收到
    confirmed_at = Column(DateTime)

    task = relationship("AITask", back_populates="logs")

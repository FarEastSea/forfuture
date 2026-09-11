"""领域模型。

导入本包即注册所有表到 Base.metadata，Alembic 的 env.py 依赖这一点。
"""
from app.domain.base import Base
from app.domain.content import ContentComment, ContentItem, ContentRevision
from app.domain.identity import (
    AccountProfileHistory,
    BrowserProfile,
    LoginCredential,
    PlatformAccount,
    Proxy,
)
from app.domain.knowledge import KnowledgeChunk
from app.domain.media import ContentMedia, MediaAsset
from app.domain.ops import (
    AgentMessage,
    AgentSession,
    AgentToolCall,
    AppSetting,
    CrawlJob,
    CrawlJobEvent,
    LLMProvider,
    MigrationAudit,
    ScheduledTask,
    TaskRun,
)

__all__ = [
    "AccountProfileHistory",
    "AgentMessage",
    "AgentSession",
    "AgentToolCall",
    "AppSetting",
    "Base",
    "BrowserProfile",
    "ContentComment",
    "ContentItem",
    "ContentMedia",
    "ContentRevision",
    "CrawlJob",
    "CrawlJobEvent",
    "KnowledgeChunk",
    "LLMProvider",
    "LoginCredential",
    "MediaAsset",
    "MigrationAudit",
    "PlatformAccount",
    "Proxy",
    "ScheduledTask",
    "TaskRun",
]

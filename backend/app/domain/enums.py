"""领域枚举。

值都用小写字符串直接落库，方便在 SQL 里直接读懂，也避免数据库枚举类型带来的迁移负担。
"""
from __future__ import annotations

from enum import StrEnum


class Platform(StrEnum):
    QQ = "qq"
    XHS = "xhs"


class ContentType(StrEnum):
    QQ_MOMENT = "qq_moment"
    XHS_NOTE = "xhs_note"


class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AVATAR = "avatar"


class MediaStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADED = "downloaded"
    MISSING = "missing"
    FAILED = "failed"
    REMOTE_ONLY = "remote_only"


class MediaRole(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    COVER = "cover"


class CredentialStatus(StrEnum):
    """登录凭据的风控状态机。

    active -> degraded -> cooldown -> relogin_pending -> expired
    任何一步成功采集都会回到 active。
    """

    ACTIVE = "active"
    DEGRADED = "degraded"
    COOLDOWN = "cooldown"
    RELOGIN_PENDING = "relogin_pending"
    EXPIRED = "expired"
    DISABLED = "disabled"


class CrawlMode(StrEnum):
    INCREMENTAL = "incremental"
    OVERWRITE = "overwrite"
    REPAIR = "repair"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChunkType(StrEnum):
    BODY = "body"
    TITLE = "title"
    COMMENT_DIGEST = "comment_digest"
    IMAGE_CAPTION = "image_caption"


class ScheduleKind(StrEnum):
    CRON = "cron"
    INTERVAL = "interval"


class TaskKind(StrEnum):
    CRAWL = "crawl"
    AGENT = "agent"
    INDEX = "index"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


ACTIVE_CREDENTIAL_STATUSES = frozenset({CredentialStatus.ACTIVE, CredentialStatus.DEGRADED})
BLOCKED_CREDENTIAL_STATUSES = frozenset(
    {
        CredentialStatus.COOLDOWN,
        CredentialStatus.RELOGIN_PENDING,
        CredentialStatus.EXPIRED,
        CredentialStatus.DISABLED,
    }
)

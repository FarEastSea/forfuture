"""平台插件协议。

每个平台实现 Authenticator / Fetcher / Parser，由 registry 按 platform 名装配。
上层采集引擎只认识 Normalized* 数据结构，不知道 QQ JSONP 或小红书 feed 的细节。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.domain.enums import ContentType, CrawlMode, MediaKind, Platform


@dataclass(slots=True)
class NormalizedMedia:
    kind: MediaKind
    url: str
    fallback_urls: list[str] = field(default_factory=list)
    duration_seconds: int | None = None
    role: str = "image"


@dataclass(slots=True)
class NormalizedComment:
    platform_comment_id: str
    parent_platform_id: str | None = None
    author_uid: str | None = None
    author_name: str | None = None
    author_avatar_url: str | None = None
    body: str | None = None
    like_count: int = 0
    sub_comment_count: int = 0
    commented_at: datetime | None = None
    ip_location: str | None = None
    reply_to_name: str | None = None
    is_author_reply: bool = False
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class NormalizedItem:
    platform: Platform
    platform_item_id: str
    content_type: ContentType
    author_uid: str | None = None
    author_name: str | None = None
    author_avatar_url: str | None = None
    title: str | None = None
    body: str | None = None
    posted_at: datetime | None = None
    edited_at: datetime | None = None
    ip_location: str | None = None
    geo_location: str | None = None
    device: str | None = None
    source_url: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    media: list[NormalizedMedia] = field(default_factory=list)
    comments: list[NormalizedComment] = field(default_factory=list)
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class CrawlPage:
    items: list[NormalizedItem]
    next_cursor: str | None = None
    exhausted: bool = False
    raw: Any = None


@dataclass(slots=True)
class AuthResult:
    ok: bool
    cookies: list[dict[str, Any]] = field(default_factory=list)
    storage_state: dict[str, Any] | None = None
    platform_uid: str | None = None
    nickname: str | None = None
    detail: str = ""


@runtime_checkable
class SignatureProvider(Protocol):
    """预留给将来的纯 HTTP 签名方案。当前小红书实现走页面 JS 自签名。"""

    async def sign(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, str]:
        ...


class Parser(Protocol):
    def parse_feed(self, payload: Any, *, target_uid: str) -> CrawlPage: ...

    def parse_item_detail(self, payload: Any) -> NormalizedItem | None: ...

    def parse_comments(self, payload: Any) -> tuple[list[NormalizedComment], str | None, bool]: ...


class Fetcher(Protocol):
    async def fetch_feed(self, target_uid: str, *, cursor: str | None = None) -> Any: ...

    async def fetch_item(self, item_id: str) -> Any: ...

    async def fetch_comments(self, item_id: str, *, cursor: str | None = None) -> Any: ...

    async def close(self) -> None: ...


class Authenticator(Protocol):
    async def probe(self, cookies: list[dict[str, Any]]) -> AuthResult: ...

    async def refresh(self, cookies: list[dict[str, Any]]) -> AuthResult: ...


@dataclass
class PlatformPlugin:
    name: Platform
    parser: Parser
    make_fetcher: Any
    authenticator: Authenticator
    needs_browser: bool = False

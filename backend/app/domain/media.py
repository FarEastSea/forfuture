"""媒体域。

关于历史资产的硬约束：迁移进来的旧记录，local_path 必须原样保留（形如
/static/qq_images/<uuid>.jpg），磁盘上的文件不做任何移动或改名。内容哈希命名只应用于
新下载的文件。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
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
from app.domain.enums import MediaStatus


class MediaAsset(Base, TimestampMixin):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(20))

    remote_url: Mapped[str | None] = mapped_column(Text)
    # 对外暴露的路径，形如 /static/qq_images/xxx.jpg
    local_path: Mapped[str | None] = mapped_column(String(500))
    content_sha256: Mapped[str | None] = mapped_column(String(64))

    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(120))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MediaStatus.PENDING.value
    )
    download_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime)

    # 备用下载地址（QQ 的 url3/url2/url1 之类多档清晰度）
    fallback_urls: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index(
            "uq_media_assets_local_path",
            "local_path",
            unique=True,
            postgresql_where=local_path.isnot(None),
        ),
        Index(
            "ix_media_assets_sha256",
            "content_sha256",
            postgresql_where=content_sha256.isnot(None),
        ),
        Index("ix_media_assets_kind_status", "kind", "status"),
    )


class ContentMedia(Base):
    """内容与媒体的关联，position 保留原始顺序。"""

    __tablename__ = "content_media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    media_asset_id: Mapped[int] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    media: Mapped[MediaAsset] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint(
            "content_item_id", "media_asset_id", "role", name="uq_content_media_item_asset_role"
        ),
        Index("ix_content_media_item_position", "content_item_id", "position"),
    )

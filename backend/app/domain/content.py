"""内容域：跨平台统一的内容条目、评论与修订历史。

QQ 说说和小红书笔记共用同一张表，平台特有字段进 extra JSONB，原始响应进 raw。
新增平台不需要建新表，知识库也只需要索引一处。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
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
from app.domain.media import ContentMedia


class ContentItem(Base, TimestampMixin):
    __tablename__ = "content_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    # 平台侧的内容 ID（QQ 的 tid、小红书的 note_id），与 platform 组合唯一
    platform_item_id: Mapped[str] = mapped_column(String(200), nullable=False)
    content_type: Mapped[str] = mapped_column(String(30), nullable=False)

    target_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_accounts.id", ondelete="SET NULL")
    )

    author_platform_uid: Mapped[str | None] = mapped_column(String(200))
    author_name: Mapped[str | None] = mapped_column(String(200))
    author_avatar_media_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL")
    )

    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime)

    ip_location: Mapped[str | None] = mapped_column(String(100))
    geo_location: Mapped[str | None] = mapped_column(String(300))
    device: Mapped[str | None] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(Text)

    # {"like": n, "comment": n, "collect": n, "share": n, "forward": n}
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # 平台特有：QQ 的 forward_content；小红书的 tags / at_users / note_type 等
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # 标题 + 正文 + 媒体 URL 的指纹，用于变更检测与知识库增量索引
    content_hash: Mapped[str | None] = mapped_column(String(64))

    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)

    comments: Mapped[list["ContentComment"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", passive_deletes=True
    )
    revisions: Mapped[list["ContentRevision"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", passive_deletes=True
    )
    media_links: Mapped[list[ContentMedia]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by=ContentMedia.position
    )

    __table_args__ = (
        UniqueConstraint("platform", "platform_item_id", name="uq_content_items_platform_item"),
        Index("ix_content_items_platform_posted_at", "platform", "posted_at"),
        Index("ix_content_items_target_posted_at", "target_account_id", "posted_at"),
        Index("ix_content_items_content_hash", "content_hash"),
    )


class ContentComment(Base):
    __tablename__ = "content_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    platform_comment_id: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_comments.id", ondelete="CASCADE")
    )

    author_platform_uid: Mapped[str | None] = mapped_column(String(200))
    author_name: Mapped[str | None] = mapped_column(String(200))
    author_avatar_media_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL")
    )

    body: Mapped[str | None] = mapped_column(Text)
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sub_comment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    commented_at: Mapped[datetime | None] = mapped_column(DateTime)
    ip_location: Mapped[str | None] = mapped_column(String(100))
    # 被回复人昵称，小红书子评论用得到
    reply_to_name: Mapped[str | None] = mapped_column(String(200))
    is_author_reply: Mapped[bool] = mapped_column(default=False, nullable=False)

    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    item: Mapped[ContentItem] = relationship(back_populates="comments")
    replies: Mapped[list["ContentComment"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan", passive_deletes=True
    )
    parent: Mapped["ContentComment | None"] = relationship(
        back_populates="replies", remote_side=[id]
    )

    __table_args__ = (
        UniqueConstraint(
            "content_item_id", "platform_comment_id", name="uq_content_comments_item_comment"
        ),
        Index("ix_content_comments_item_time", "content_item_id", "commented_at"),
    )


class ContentRevision(Base):
    """内容修订历史。旧模型存在 edit_history JSON 列里，这里拆成可查询的行。"""

    __tablename__ = "content_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    revision_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # {"body": {"old": "...", "new": "..."}, "title": {...}}
    changed_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    item: Mapped[ContentItem] = relationship(back_populates="revisions")

    __table_args__ = (
        UniqueConstraint(
            "content_item_id", "revision_index", name="uq_content_revisions_item_index"
        ),
    )

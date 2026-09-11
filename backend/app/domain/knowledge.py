"""知识库域：分块与向量/全文双路索引。

嵌入向量存 BYTEA（float32 小端打包），因为目标 PostgreSQL 上不一定装得了 pgvector。
装了 pgvector 时会由条件迁移额外加一列 embedding_vec 走 HNSW 索引；检索层按可用性选路。
中文全文检索走 jieba 分词后写入 simple 配置的 tsvector，不依赖 zhparser 之类的服务端扩展。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.base import Base, TimestampMixin


class KnowledgeChunk(Base, TimestampMixin):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE")
    )
    chunk_type: Mapped[str] = mapped_column(String(30), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_model: Mapped[str | None] = mapped_column(String(120))
    embedding_dim: Mapped[int | None] = mapped_column(Integer)

    # tsvector 没有对应的 Python 类型，注解用 Any，列类型由 mapped_column 显式指定
    lexemes: Mapped[Any] = mapped_column(TSVECTOR, nullable=True)

    # 冗余一份检索过滤要用的字段，避免每次都 JOIN content_items
    platform: Mapped[str | None] = mapped_column(String(20))
    author_name: Mapped[str | None] = mapped_column(String(200))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)

    # 对应 content_items.content_hash，值没变就不重新嵌入
    source_hash: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "content_item_id",
            "chunk_type",
            "chunk_index",
            name="uq_knowledge_chunks_item_type_index",
        ),
        Index("ix_knowledge_chunks_lexemes", "lexemes", postgresql_using="gin"),
        Index("ix_knowledge_chunks_platform_posted", "platform", "posted_at"),
        Index("ix_knowledge_chunks_source_hash", "source_hash"),
    )

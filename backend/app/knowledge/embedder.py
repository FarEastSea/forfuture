"""OpenAI 兼容嵌入。向量以 float32 小端打包进 BYTEA。"""
from __future__ import annotations

import struct
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConfigurationError
from app.domain.ops import LLMProvider

EMBEDDING_REQUEST_BATCH_SIZE = 16


def pack_embedding(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def unpack_embedding(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def active_provider(session: AsyncSession) -> LLMProvider:
    row = (
        await session.execute(select(LLMProvider).where(LLMProvider.is_active.is_(True)))
    ).scalar_one_or_none()
    if row is None:
        raise ConfigurationError("没有启用的 LLM 供应商")
    return row


async def embed_texts(session: AsyncSession, texts: list[str]) -> tuple[list[list[float]], str]:
    if not texts:
        return [], ""
    provider = await active_provider(session)
    if not provider.embed_model:
        raise ConfigurationError("当前 LLM 供应商没有配置 embed_model")
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=provider.api_key, base_url=provider.api_base)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBEDDING_REQUEST_BATCH_SIZE):
        batch = texts[start : start + EMBEDDING_REQUEST_BATCH_SIZE]
        response = await client.embeddings.create(model=provider.embed_model, input=batch)
        vectors.extend(list(item.embedding) for item in response.data)
    return vectors, provider.embed_model

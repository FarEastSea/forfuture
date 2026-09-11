"""混合检索：全文 + 向量余弦，RRF 融合。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.settings_store import settings_store
from app.domain.knowledge import KnowledgeChunk
from app.knowledge.embedder import cosine, embed_texts, unpack_embedding
from app.knowledge.indexer import _jieba_lexemes

logger = get_logger("app.knowledge")


async def search(
    session: AsyncSession,
    query: str,
    *,
    platform: str | None = None,
    author: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    cfg = await settings_store.load_all(session)
    k = top_k or int(cfg.get("kb_retrieval_top_k") or 12)
    rrf_k = int(cfg.get("kb_rrf_k") or 60)

    conditions = []
    if platform:
        conditions.append(KnowledgeChunk.platform == platform)
    if author:
        conditions.append(KnowledgeChunk.author_name.ilike(f"%{author}%"))
    if since:
        conditions.append(KnowledgeChunk.posted_at >= since)
    if until:
        conditions.append(KnowledgeChunk.posted_at <= until)

    stmt = select(KnowledgeChunk).where(*conditions) if conditions else select(KnowledgeChunk)
    rows = (await session.execute(stmt.limit(2000))).scalars().all()
    if not rows:
        return []

    lex_query = _jieba_lexemes(query)
    lexical_ranked = sorted(
        rows,
        key=lambda row: _lexical_score(lex_query, row.text),
        reverse=True,
    )

    vector_ranked: list[KnowledgeChunk] = []
    if any(row.embedding for row in rows):
        try:
            (query_vec,), _ = await embed_texts(session, [query])
            scored = []
            for row in rows:
                if not row.embedding:
                    continue
                scored.append((cosine(query_vec, unpack_embedding(row.embedding)), row))
            vector_ranked = [row for _, row in sorted(scored, key=lambda x: x[0], reverse=True)]
        except Exception as exc:
            logger.warning("向量检索不可用，本次退化为纯全文检索: %s", exc)

    fused: dict[int, float] = {}
    for rank, row in enumerate(lexical_ranked):
        fused[row.id] = fused.get(row.id, 0) + 1 / (rrf_k + rank + 1)
    for rank, row in enumerate(vector_ranked):
        fused[row.id] = fused.get(row.id, 0) + 1 / (rrf_k + rank + 1)

    by_id = {row.id: row for row in rows}
    ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:k]
    return [
        {
            "chunk_id": chunk_id,
            "score": score,
            "content_item_id": by_id[chunk_id].content_item_id,
            "chunk_type": by_id[chunk_id].chunk_type,
            "text": by_id[chunk_id].text,
            "platform": by_id[chunk_id].platform,
            "author_name": by_id[chunk_id].author_name,
            "posted_at": by_id[chunk_id].posted_at,
        }
        for chunk_id, score in ordered
        if chunk_id in by_id
    ]


def _lexical_score(query: str, text: str) -> float:
    tokens = [t for t in query.split() if t]
    if not tokens:
        return 0.0
    haystack = text or ""
    return sum(haystack.count(token) for token in tokens) / len(tokens)

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from sqlalchemy import select

from app.api.deps import AdminDep, SessionDep
from app.domain.content import ContentItem
from app.knowledge.indexer import (
    backfill_missing_embeddings,
    embedding_coverage,
    index_item,
    probe_embedding,
)
from app.knowledge.retriever import search

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    platform: str | None = None
    author: str | None = None
    top_k: int = 12


@router.post("/search", summary="知识库检索")
async def search_kb(payload: SearchRequest, session: SessionDep, _: AdminDep):
    hits = await search(
        session,
        payload.query,
        platform=payload.platform,
        author=payload.author,
        top_k=payload.top_k,
    )
    return {"items": hits}


@router.post("/index", summary="对存量内容回填知识库索引")
async def backfill_index(session: SessionDep, _: AdminDep, force: bool = False):
    rows = (await session.execute(select(ContentItem).order_by(ContentItem.id))).scalars().all()
    embedding_available, embedding_error = await probe_embedding(session)
    chunks = 0
    for item in rows:
        chunks += await index_item(
            session,
            item,
            force=force,
            use_embeddings=embedding_available,
        )
    embeddings_added = (
        await backfill_missing_embeddings(session) if embedding_available else 0
    )
    await session.commit()
    total_chunks, embedded_chunks = await embedding_coverage(session)
    fulltext_only_chunks = total_chunks - embedded_chunks
    return {
        "items": len(rows),
        "indexed_chunks": chunks,
        "embeddings_added": embeddings_added,
        "total_chunks": total_chunks,
        "embedded_chunks": embedded_chunks,
        "fulltext_only_chunks": fulltext_only_chunks,
        "embedding_status": "ready" if fulltext_only_chunks == 0 else "degraded",
        "embedding_provider_available": embedding_available,
        "embedding_error": embedding_error,
        "warning": (
            None
            if fulltext_only_chunks == 0
            else "嵌入不可用或部分失败；这些分块当前只能参与全文检索"
        ),
    }

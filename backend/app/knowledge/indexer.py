"""增量索引：content_hash 没变就跳过。"""
from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.settings_store import settings_store
from app.domain.content import ContentComment, ContentItem
from app.domain.knowledge import KnowledgeChunk
from app.knowledge.chunker import chunks_for_item
from app.knowledge.embedder import embed_texts, pack_embedding

logger = get_logger("app.knowledge")


async def embedding_coverage(session: AsyncSession) -> tuple[int, int]:
    """返回知识库总分块数与已带向量的分块数。"""
    total, embedded = (
        await session.execute(
            select(
                func.count(KnowledgeChunk.id),
                func.count(KnowledgeChunk.embedding),
            )
        )
    ).one()
    return int(total or 0), int(embedded or 0)


async def backfill_missing_embeddings(session: AsyncSession, *, batch_size: int = 16) -> int:
    """只补齐缺失向量，不重建全文分块，也不重复发送已有向量的文本。"""
    rows = list(
        (
            await session.execute(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.embedding.is_(None))
                .order_by(KnowledgeChunk.id)
            )
        )
        .scalars()
        .all()
    )
    updated = 0
    for start in range(0, len(rows), max(1, batch_size)):
        batch = rows[start : start + max(1, batch_size)]
        vectors, model = await embed_texts(session, [row.text for row in batch])
        if len(vectors) != len(batch) or any(not vector for vector in vectors):
            raise RuntimeError("嵌入供应商返回的向量数量或内容不完整")
        for row, vector in zip(batch, vectors):
            row.embedding = pack_embedding(vector)
            row.embedding_model = model
            row.embedding_dim = len(vector)
            updated += 1
        await session.flush()
    return updated


async def probe_embedding(session: AsyncSession) -> tuple[bool, str | None]:
    """批量索引前只探测一次嵌入能力，避免故障时对供应商重复发请求。"""
    try:
        vectors, _ = await embed_texts(session, ["知识库嵌入能力检测"])
        if not vectors or not vectors[0]:
            return False, "EmptyEmbedding"
    except Exception as exc:
        return False, type(exc).__name__
    return True, None


def _jieba_lexemes(text: str) -> str:
    try:
        import jieba
    except ImportError:
        return text
    return " ".join(token for token in jieba.cut(text) if token.strip())


async def index_item(
    session: AsyncSession,
    item: ContentItem,
    *,
    force: bool = False,
    use_embeddings: bool = True,
) -> int:
    cfg = await settings_store.load_all(session)
    existing = (
        (
            await session.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.content_item_id == item.id)
            )
        )
        .scalars()
        .all()
    )
    if not force and existing and existing[0].source_hash == item.content_hash:
        return 0

    comments = [
        c.body
        for c in (
            await session.execute(
                select(ContentComment).where(ContentComment.content_item_id == item.id)
            )
        )
        .scalars()
        .all()
        if c.body
    ]
    pieces = chunks_for_item(
        title=item.title,
        body=item.body,
        comments=comments,
        image_captions=[],
        size=int(cfg.get("kb_chunk_size") or 480),
        overlap=int(cfg.get("kb_chunk_overlap") or 80),
    )
    await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.content_item_id == item.id))
    if not pieces:
        return 0

    texts = [p[1] for p in pieces]
    if not use_embeddings:
        vectors, model = [[] for _ in texts], None
    else:
        try:
            vectors, model = await embed_texts(session, texts)
        except Exception as exc:
            # 单条增量索引仍允许全文降级；批量回填会先 probe，一次失败后不再重复请求。
            logger.warning(
                "嵌入失败，本次只建全文索引（向量检索将不可用） item=%s: %s", item.id, exc
            )
            vectors, model = [[] for _ in texts], None

    for (chunk_type, text, index), vector in zip(pieces, vectors):
        session.add(
            KnowledgeChunk(
                content_item_id=item.id,
                chunk_type=chunk_type,
                chunk_index=index,
                text=text,
                token_count=len(text),
                embedding=pack_embedding(vector) if vector else None,
                embedding_model=model,
                embedding_dim=len(vector) if vector else None,
                # TSVECTOR 的文本输入有独立语法，普通分词字符串遇到英文撇号等字符会
                # 直接解析失败。交给 PostgreSQL 构造，既能正确转义也能填充 GIN 索引。
                lexemes=func.to_tsvector("simple", _jieba_lexemes(text)),
                platform=item.platform,
                author_name=item.author_name,
                posted_at=item.posted_at,
                source_hash=item.content_hash,
                meta={},
            )
        )
    await session.flush()
    return len(pieces)

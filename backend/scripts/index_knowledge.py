"""对存量 content_items 回填知识库分块。可在远端 backend/current 下执行。"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.db import session_factory  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.domain.content import ContentItem  # noqa: E402
from app.knowledge.indexer import (  # noqa: E402
    backfill_missing_embeddings,
    embedding_coverage,
    index_item,
    probe_embedding,
)

logger = get_logger("scripts.index_knowledge")


async def main_async(force: bool) -> int:
    async with session_factory() as session:
        rows = (await session.execute(select(ContentItem).order_by(ContentItem.id))).scalars().all()
        embedding_available, embedding_error = await probe_embedding(session)
        if not embedding_available:
            logger.error(
                "嵌入供应商探测失败（%s），本次回填将只建立全文索引",
                embedding_error,
            )
        total = 0
        for item in rows:
            total += await index_item(
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
    print(
        f"indexed_chunks={total} embeddings_added={embeddings_added} items={len(rows)} total_chunks={total_chunks} "
        f"embedded_chunks={embedded_chunks} fulltext_only_chunks={fulltext_only_chunks}"
    )
    if fulltext_only_chunks:
        logger.error(
            "知识库回填以降级状态完成：%s 个分块没有嵌入向量，只能参与全文检索",
            fulltext_only_chunks,
        )
        return 2
    return 0


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    return asyncio.run(main_async(parser.parse_args().force))


if __name__ == "__main__":
    raise SystemExit(main())

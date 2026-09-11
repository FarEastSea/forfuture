"""知识库向量补齐的增量行为测试。"""
from __future__ import annotations

import pytest

from app.knowledge import indexer
from app.knowledge.embedder import unpack_embedding


class _Scalars:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return _Scalars(self.rows)


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.flushes = 0

    async def execute(self, _statement):
        return _Result(self.rows)

    async def flush(self):
        self.flushes += 1


class _Chunk:
    def __init__(self, text):
        self.text = text
        self.embedding = None
        self.embedding_model = None
        self.embedding_dim = None


@pytest.mark.asyncio
async def test_backfill_missing_embeddings_batches_without_rebuilding_chunks(monkeypatch):
    rows = [_Chunk("一"), _Chunk("二"), _Chunk("三")]
    session = _Session(rows)
    sent = []

    async def fake_embed_texts(_session, texts):
        sent.append(list(texts))
        return [[float(len(text)), 1.0] for text in texts], "embedding-test"

    monkeypatch.setattr(indexer, "embed_texts", fake_embed_texts)
    updated = await indexer.backfill_missing_embeddings(session, batch_size=2)

    assert updated == 3
    assert sent == [["一", "二"], ["三"]]
    assert session.flushes == 2
    assert all(row.embedding_model == "embedding-test" for row in rows)
    assert all(row.embedding_dim == 2 for row in rows)
    assert unpack_embedding(rows[0].embedding) == pytest.approx([1.0, 1.0])

from types import SimpleNamespace

import pytest

from app.knowledge.chunker import chunk_text
from app.knowledge import retriever
from app.platforms.qq.parser import compute_g_tk


def test_chunk_overlap():
    parts = chunk_text("abcdefghij" * 60, size=100, overlap=20)
    assert len(parts) > 1
    assert parts[0][-20:] == parts[1][:20]


def test_gtk_stable():
    assert compute_g_tk("p_skey_sample") == compute_g_tk("p_skey_sample")


@pytest.mark.asyncio
async def test_search_skips_embedding_provider_when_index_has_no_vectors(monkeypatch):
    rows = [
        SimpleNamespace(
            id=1,
            content_item_id=10,
            chunk_type="body",
            text="济宁 毛娘 后续",
            embedding=None,
            platform="xhs",
            author_name="tester",
            posted_at=None,
        )
    ]

    class Result:
        def scalars(self):
            return self

        def all(self):
            return rows

    class Session:
        async def execute(self, _statement):
            return Result()

    async def load_settings(_session):
        return {"kb_retrieval_top_k": 12, "kb_rrf_k": 60}

    async def unexpected_embedding_call(*_args, **_kwargs):
        raise AssertionError("没有已存向量时不应调用嵌入供应商")

    monkeypatch.setattr(retriever.settings_store, "load_all", load_settings)
    monkeypatch.setattr(retriever, "embed_texts", unexpected_embedding_call)

    hits = await retriever.search(Session(), "济宁 毛娘", platform="xhs")

    assert [hit["content_item_id"] for hit in hits] == [10]

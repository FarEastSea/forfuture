"""嵌入请求的供应商批量上限回归测试。"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.knowledge import embedder


@pytest.mark.asyncio
async def test_embed_texts_splits_requests_at_gateway_batch_limit(monkeypatch):
    provider = SimpleNamespace(
        api_key="test-key",
        api_base="https://example.invalid/v1",
        embed_model="embedding-test",
    )
    sent: list[list[str]] = []

    async def fake_active_provider(_session):
        return provider

    class _Embeddings:
        async def create(self, *, model, input):
            assert model == provider.embed_model
            sent.append(list(input))
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[float(len(text))]) for text in input]
            )

    class _Client:
        def __init__(self, **_kwargs):
            self.embeddings = _Embeddings()

    monkeypatch.setattr(embedder, "active_provider", fake_active_provider)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=_Client))

    texts = [str(index) for index in range(33)]
    vectors, model = await embedder.embed_texts(object(), texts)

    assert [len(batch) for batch in sent] == [16, 16, 1]
    assert len(vectors) == len(texts)
    assert model == provider.embed_model

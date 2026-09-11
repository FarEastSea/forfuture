"""内容落库的异步 ORM 边界回归测试。"""
from __future__ import annotations

import pytest

from app.crawl import persist
from app.domain.enums import ContentType, MediaKind, Platform
from app.domain.media import ContentMedia
from app.platforms.base import NormalizedItem, NormalizedMedia


class _Scalars:
    def all(self):
        return []


class _Result:
    def scalars(self):
        return _Scalars()


class _Session:
    def __init__(self):
        self.added = []

    async def execute(self, _statement):
        return _Result()

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


class _Row:
    id = 42

    @property
    def media_links(self):
        raise AssertionError("异步落库不得访问可能触发懒加载的 ORM relationship")


@pytest.mark.asyncio
async def test_sync_media_uses_explicit_query_instead_of_lazy_relationship(monkeypatch):
    async def fake_download_asset(*_args, **_kwargs):
        return type("Asset", (), {"id": 7})()

    monkeypatch.setattr(persist, "download_asset", fake_download_asset)
    session = _Session()
    item = NormalizedItem(
        platform=Platform.QQ,
        platform_item_id="qq-item",
        content_type=ContentType.QQ_MOMENT,
        media=[NormalizedMedia(kind=MediaKind.IMAGE, url="https://example.invalid/a.jpg")],
    )

    await persist._sync_media(session, _Row(), item, overwrite=False)

    links = [value for value in session.added if isinstance(value, ContentMedia)]
    assert len(links) == 1
    assert links[0].content_item_id == 42
    assert links[0].media_asset_id == 7

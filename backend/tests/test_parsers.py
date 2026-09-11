"""解析器回归测试：用固化的真实响应形状，保证重写不丢字段。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.errors import AntiBotError, CredentialExpiredError
from app.platforms.qq.constants import JSONP_CALLBACK
from app.platforms.qq.client import QQFetcher
from app.platforms.qq.parser import QQParser, compute_g_tk, unwrap_jsonp
from app.platforms.xhs.auth import XHSAuthenticator
from app.platforms.xhs.client import _raise_for_status
from app.platforms.xhs.parser import XHSParser, clean_ip_location, parse_count, pick_image_url, pick_video_url

FIXTURES = Path(__file__).parent / "fixtures"


class _Page:
    def __init__(self, result):
        self.result = result

    async def evaluate(self, _script):
        return self.result


@pytest.mark.asyncio
async def test_xhs_auth_probe_rejects_antibot_response():
    result = await XHSAuthenticator(
        _Page({"status": 406, "body": {"success": False, "code": -1}})
    ).probe([])
    assert result.ok is False
    assert result.detail == "auth-probe-rejected:406"


def test_xhs_461_is_antibot_not_expired():
    with pytest.raises(AntiBotError):
        _raise_for_status(461, {"success": True, "code": 0})
    with pytest.raises(CredentialExpiredError):
        _raise_for_status(401, None)


def test_gtk_algorithm():
    assert compute_g_tk("abc") == 193485963
    assert compute_g_tk("") == 5381


def test_qq_jsonp_and_fields():
    raw = (FIXTURES / "qq_msglist.jsonp").read_text(encoding="utf-8")
    page = QQParser().parse_feed(raw, target_uid="123456")
    assert len(page.items) == 1
    item = page.items[0]
    assert item.platform_item_id == "tid-1"
    assert item.body == "hello world"
    assert item.extra["forward_content"] == "forwarded"
    assert item.device == "荣耀90 GT (5G)"
    assert item.geo_location == "上海"
    assert item.metrics["like"] == 3
    assert item.media[0].url.endswith("url3.jpg")
    assert item.media[0].fallback_urls[-1].endswith("url1.jpg")
    assert item.comments[0].body == "nice"


def test_qq_expired_html():
    from app.core.errors import CredentialExpiredError

    with pytest.raises(CredentialExpiredError):
        unwrap_jsonp("<html>login</html>")


def test_qq_valid_jsonp_with_login_fields_is_not_expired():
    raw = f'{JSONP_CALLBACK}({{"code":0,"logininfo":{{"islogin":1}},"msglist":[]}})'
    payload = unwrap_jsonp(raw)
    assert payload["code"] == 0
    assert payload["logininfo"]["islogin"] == 1


@pytest.mark.asyncio
async def test_qq_probe_uses_same_jsonp_contract_as_crawl(monkeypatch):
    fetcher = QQFetcher([{"name": "p_skey", "value": "test"}])
    captured = {}

    async def fake_get(_url, params):
        captured.update(params)
        return f'{JSONP_CALLBACK}({{"code":0}})'

    monkeypatch.setattr(fetcher, "_get", fake_get)
    try:
        assert await fetcher.probe_jsonp("123") == f'{JSONP_CALLBACK}({{"code":0}})'
    finally:
        await fetcher.close()
    assert captured["callback"] == JSONP_CALLBACK
    assert captured["format"] == "jsonp"


def test_xhs_image_and_video_and_counts():
    note = json.loads((FIXTURES / "xhs_note.json").read_text(encoding="utf-8"))
    item = XHSParser().parse_item_detail({"data": {"items": [{"note_card": note}]}})
    assert item is not None
    assert item.title == "周末出行"
    assert item.metrics["like"] == 12000
    assert item.media[0].url.endswith("large.jpg")
    assert "!nd_dft" not in item.media[0].url
    video = next(m for m in item.media if m.kind.value == "video")
    assert "h265" in video.url
    assert clean_ip_location("IP属地：江苏") == "江苏"
    assert clean_ip_location("回到顶部") == ""
    assert parse_count("1.2万") == 12000


def test_xhs_comments_cursor():
    payload = json.loads((FIXTURES / "xhs_comments.json").read_text(encoding="utf-8"))
    comments, cursor, exhausted = XHSParser().parse_comments(payload)
    assert len(comments) == 2
    assert comments[1].parent_platform_id == "c1"
    assert cursor == "next-cursor"
    assert exhausted is False

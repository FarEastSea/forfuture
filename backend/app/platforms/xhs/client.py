"""小红书页面上下文 fetch 客户端。

签名由页面自己的 window._webmsxyw 计算，不在 Python 侧重放混淆 JS。
DOM 抓取只作为最后兜底，由 crawl engine 决定何时降级。
"""
from __future__ import annotations

from typing import Any

from app.core.errors import AntiBotError, CredentialExpiredError, TransientCrawlError

FEED_SCRIPT = """
async ({ userId, cursor }) => {
  const apiPath = '/api/sns/web/v1/user_posted?num=30&cursor=' + encodeURIComponent(cursor || '')
    + '&user_id=' + encodeURIComponent(userId) + '&image_formats=jpg,webp,avif';
  const headers = {};
  if (typeof window._webmsxyw === 'function') {
    const signed = window._webmsxyw(apiPath);
    if (signed) {
      if (signed['X-s'] || signed['x-s']) headers['X-s'] = signed['X-s'] || signed['x-s'];
      if (signed['X-t'] || signed['x-t']) headers['X-t'] = String(signed['X-t'] || signed['x-t']);
    }
  }
  const response = await fetch('https://edith.xiaohongshu.com' + apiPath, {
    method: 'GET',
    credentials: 'include',
    headers,
  });
  const status = response.status;
  const body = await response.json().catch(() => null);
  return { status, body };
}
"""

DETAIL_SCRIPT = """
async ({ noteId }) => {
  const apiPath = '/api/sns/web/v1/feed';
  const payload = { source_note_id: noteId, image_formats: ['jpg', 'webp', 'avif'], extra: { need_body_topic: 1 } };
  const headers = { 'Content-Type': 'application/json;charset=UTF-8' };
  if (typeof window._webmsxyw === 'function') {
    const signed = window._webmsxyw(apiPath, payload);
    if (signed) {
      if (signed['X-s'] || signed['x-s']) headers['X-s'] = signed['X-s'] || signed['x-s'];
      if (signed['X-t'] || signed['x-t']) headers['X-t'] = String(signed['X-t'] || signed['x-t']);
    }
  }
  const response = await fetch('https://edith.xiaohongshu.com' + apiPath, {
    method: 'POST',
    credentials: 'include',
    headers,
    body: JSON.stringify(payload),
  });
  return { status: response.status, body: await response.json().catch(() => null) };
}
"""

COMMENTS_SCRIPT = """
async ({ noteId, cursor }) => {
  const apiPath = '/api/sns/web/v2/comment/page?note_id=' + encodeURIComponent(noteId)
    + '&cursor=' + encodeURIComponent(cursor || '')
    + '&top_comment_id=&image_formats=jpg,webp,avif';
  const headers = {};
  if (typeof window._webmsxyw === 'function') {
    const signed = window._webmsxyw(apiPath);
    if (signed) {
      if (signed['X-s'] || signed['x-s']) headers['X-s'] = signed['X-s'] || signed['x-s'];
      if (signed['X-t'] || signed['x-t']) headers['X-t'] = String(signed['X-t'] || signed['x-t']);
    }
  }
  const response = await fetch('https://edith.xiaohongshu.com' + apiPath, {
    method: 'GET',
    credentials: 'include',
    headers,
  });
  return { status: response.status, body: await response.json().catch(() => null) };
}
"""


class PageSignatureProvider:
    """当前实现：签名完全交给页面 JS。接口预留给将来的纯 HTTP 方案。"""

    async def sign(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, str]:
        return {}


def _raise_for_status(status: int, body: Any) -> None:
    if status == 401:
        raise CredentialExpiredError(f"小红书登录态失效 (HTTP {status})")
    if status in {406, 461}:
        raise AntiBotError(f"小红书风控拒绝请求（HTTP {status}），需要页面交互或刷新会话")
    if status == 429:
        from app.core.errors import RateLimitedError

        raise RateLimitedError("小红书限流", retry_after_seconds=30)
    if status >= 500:
        raise TransientCrawlError(f"小红书上游错误 HTTP {status}")
    if isinstance(body, dict) and body.get("code") in {-101, -1}:
        raise CredentialExpiredError(f"小红书业务码 {body.get('code')}: {body.get('msg')}")


class XHSFetcher:
    def __init__(self, page: Any, *, signature: PageSignatureProvider | None = None) -> None:
        self._page = page
        self.signature = signature or PageSignatureProvider()

    async def fetch_feed(self, target_uid: str, *, cursor: str | None = None) -> dict[str, Any]:
        result = await self._page.evaluate(FEED_SCRIPT, {"userId": target_uid, "cursor": cursor or ""})
        _raise_for_status(int(result.get("status") or 0), result.get("body"))
        return result.get("body") or {}

    async def fetch_item(self, item_id: str) -> dict[str, Any]:
        result = await self._page.evaluate(DETAIL_SCRIPT, {"noteId": item_id})
        _raise_for_status(int(result.get("status") or 0), result.get("body"))
        return result.get("body") or {}

    async def fetch_comments(self, item_id: str, *, cursor: str | None = None) -> dict[str, Any]:
        result = await self._page.evaluate(COMMENTS_SCRIPT, {"noteId": item_id, "cursor": cursor or ""})
        _raise_for_status(int(result.get("status") or 0), result.get("body"))
        return result.get("body") or {}

    async def close(self) -> None:
        return None

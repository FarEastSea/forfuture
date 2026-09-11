"""小红书登录态探测：走 /api/sns/web/v2/user/me，拒绝 guest。"""
from __future__ import annotations

from typing import Any

from app.platforms.base import AuthResult

ME_SCRIPT = """
async () => {
  const response = await fetch('https://edith.xiaohongshu.com/api/sns/web/v2/user/me', {
    credentials: 'include',
  });
  return { status: response.status, body: await response.json().catch(() => null) };
}
"""


class XHSAuthenticator:
    def __init__(self, page: Any | None = None) -> None:
        self._page = page

    async def probe(self, cookies: list[dict[str, Any]]) -> AuthResult:
        if self._page is None:
            names = {c.get("name") for c in cookies}
            ok = bool(names & {"web_session", "access-token-v2", "customer-sso-sid"})
            return AuthResult(ok=ok, cookies=cookies, detail="cookie-presence" if ok else "missing-session")

        result = await self._page.evaluate(ME_SCRIPT)
        body = result.get("body") or {}
        data = body.get("data") or {}
        status = int(result.get("status") or 0)
        if status == 401 or data.get("guest") is True:
            return AuthResult(ok=False, cookies=cookies, detail="credential-expired")
        if status != 200 or body.get("success") is False or body.get("code") not in {None, 0}:
            return AuthResult(ok=False, cookies=cookies, detail=f"auth-probe-rejected:{status}")
        platform_uid = str(data.get("user_id") or "") or None
        if not platform_uid:
            return AuthResult(ok=False, cookies=cookies, detail="identity-missing")
        return AuthResult(
            ok=True,
            cookies=cookies,
            platform_uid=platform_uid,
            nickname=data.get("nickname"),
            detail="ok",
        )

    async def refresh(self, cookies: list[dict[str, Any]]) -> AuthResult:
        return await self.probe(cookies)

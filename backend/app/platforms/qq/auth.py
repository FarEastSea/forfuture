"""QQ 登录凭据探测与刷新。

QR 扫码、NapCat 导入仍由 legacy 路由提供，新栈先接管"凭据是否还能用"和 Cookie 刷新探测。
"""
from __future__ import annotations

from typing import Any

from app.core.errors import CredentialExpiredError
from app.platforms.base import AuthResult
from app.platforms.qq.client import QQFetcher
from app.platforms.qq.parser import interpret_code, unwrap_jsonp


def _uin_from_cookies(cookies: list[dict[str, Any]]) -> str:
    mapping = {c.get("name"): str(c.get("value") or "") for c in cookies}
    raw = mapping.get("uin") or mapping.get("p_uin") or ""
    return raw.lstrip("o").lstrip("0")


class QQAuthenticator:
    async def probe(self, cookies: list[dict[str, Any]]) -> AuthResult:
        uin = _uin_from_cookies(cookies)
        if not uin:
            return AuthResult(ok=False, detail="Cookie 中没有 uin")
        fetcher = QQFetcher(cookies)
        try:
            text = await fetcher.probe_jsonp(uin)
            data = unwrap_jsonp(text)
            interpret_code(data)
            return AuthResult(ok=True, cookies=cookies, platform_uid=uin, detail="ok")
        except CredentialExpiredError as exc:
            return AuthResult(ok=False, cookies=cookies, platform_uid=uin, detail=str(exc))
        except Exception as exc:
            return AuthResult(ok=False, cookies=cookies, platform_uid=uin, detail=str(exc))
        finally:
            await fetcher.close()

    async def refresh(self, cookies: list[dict[str, Any]]) -> AuthResult:
        # Cookie 刷新需要浏览器走 SSO，当前仍由 legacy refresh_qq_cookies 完成。
        # 这里先做探测，成功即视为仍然有效。
        return await self.probe(cookies)

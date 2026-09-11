"""QQ 空间 HTTP 客户端。用 curl_cffi impersonate chrome，拿到真实 TLS 指纹。"""
from __future__ import annotations

from typing import Any

from app.core.errors import CredentialExpiredError, TransientCrawlError
from app.platforms.qq import constants as C
from app.platforms.qq.parser import compute_g_tk, extract_gtk_source


def _cookie_header(cookies: list[dict[str, Any]]) -> dict[str, str]:
    return {item["name"]: str(item["value"]) for item in cookies if item.get("name")}


class QQFetcher:
    def __init__(self, cookies: list[dict[str, Any]], *, proxy: str | None = None) -> None:
        self._cookies = cookies
        self._proxy = proxy
        self._session: Any = None
        source = extract_gtk_source(cookies)
        if not source:
            raise CredentialExpiredError("登录凭据缺少 p_skey/skey，无法计算 g_tk")
        self._g_tk = compute_g_tk(source)

    async def _session_obj(self):
        if self._session is None:
            from curl_cffi.requests import AsyncSession

            kwargs: dict[str, Any] = {"impersonate": "chrome", "timeout": 30}
            if self._proxy:
                kwargs["proxy"] = self._proxy
            self._session = AsyncSession(**kwargs)
        return self._session

    async def _get(self, url: str, params: dict[str, Any]) -> str:
        session = await self._session_obj()
        try:
            response = await session.get(
                url,
                params=params,
                cookies=_cookie_header(self._cookies),
                headers={"Referer": "https://user.qzone.qq.com/"},
            )
        except Exception as exc:
            raise TransientCrawlError(f"QQ 空间请求失败: {exc}") from exc
        return response.text

    async def fetch_feed(self, target_uid: str, *, cursor: str | None = None) -> str:
        pos = int(cursor or 0)
        return await self._get(
            C.MSGLIST_URL,
            {
                "uin": target_uid,
                "ftype": 0,
                "sort": 0,
                "pos": pos,
                "num": C.PAGE_SIZE,
                "replynum": 100,
                "g_tk": self._g_tk,
                "callback": C.JSONP_CALLBACK,
                "code_version": 1,
                "format": "jsonp",
                "need_private_comment": 1,
            },
        )

    async def fetch_item(self, item_id: str) -> str:
        # QQ 详情接口需要 uin+tid，这里用 "uin:tid" 约定
        uin, _, tid = item_id.partition(":")
        return await self.fetch_comments(tid, cursor="0", uin=uin)

    async def fetch_comments(self, item_id: str, *, cursor: str | None = None, uin: str | None = None) -> str:
        pos = int(cursor or 0)
        params = {
            "uin": uin or "",
            "tid": item_id,
            "ftype": 0,
            "sort": 0,
            "pos": pos,
            "num": C.COMMENT_PAGE_SIZE,
            "replynum": 100,
            "g_tk": self._g_tk,
            "callback": C.JSONP_CALLBACK,
            "code_version": 1,
            "format": "jsonp",
            "need_private_comment": 1,
        }
        return await self._get(C.MSGDETAIL_URL, params)

    async def probe_jsonp(self, uin: str) -> str:
        """用与正式采集相同的确定性 JSONP 协议探测凭据。"""
        return await self._get(
            C.MSGLIST_URL,
            {
                "uin": uin,
                "ftype": 0,
                "sort": 0,
                "pos": 0,
                "num": 1,
                "g_tk": self._g_tk,
                "callback": C.JSONP_CALLBACK,
                "code_version": 1,
                "format": "jsonp",
                "need_private_comment": 1,
            },
        )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

"""外部媒体代理。

QQ 和小红书的 CDN 都要求特定 Referer/Origin，浏览器直接取图会被拒，所以由后端转发并
补齐请求头。只允许两个平台的域名，跳转目标也要重新校验，避免变成开放代理。
"""
from __future__ import annotations

from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter()

QQ_HOST_SUFFIXES = ("qq.com", "qpic.cn", "qlogo.cn", "gtimg.cn", "gtimg.com")
XHS_HOST_SUFFIXES = ("xiaohongshu.com", "xhscdn.com")
ALLOWED_SUFFIXES = QQ_HOST_SUFFIXES + XHS_HOST_SUFFIXES

MAX_REDIRECTS = 5
MAX_MEDIA_BYTES = 300 * 1024 * 1024
PASSTHROUGH_HEADERS = ("accept-ranges", "content-length", "content-range", "cache-control")


def _host_matches(hostname: str, suffixes: tuple[str, ...]) -> bool:
    return any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in suffixes)


def _is_allowed(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and bool(hostname) and _host_matches(hostname, ALLOWED_SUFFIXES)


def build_headers(url: str, range_header: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        )
    }
    hostname = (urlparse(url).hostname or "").lower()

    if _host_matches(hostname, QQ_HOST_SUFFIXES):
        headers["Referer"] = "https://user.qzone.qq.com/"

    if _host_matches(hostname, XHS_HOST_SUFFIXES):
        headers.update(
            {
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Origin": "https://www.xiaohongshu.com",
                "Referer": "https://www.xiaohongshu.com/",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "cross-site",
            }
        )

    if range_header:
        headers["Range"] = range_header
    return headers


async def proxy_media(url: str, request: Request) -> StreamingResponse:
    if not _is_allowed(url):
        raise HTTPException(status_code=400, detail="不允许代理该地址")

    range_header = request.headers.get("range")
    if range_header and not range_header.lower().startswith("bytes="):
        range_header = None

    client = httpx.AsyncClient(timeout=30, follow_redirects=False)
    response: httpx.Response | None = None
    try:
        current_url = url
        for _ in range(MAX_REDIRECTS + 1):
            upstream = client.build_request("GET", current_url, headers=build_headers(current_url, range_header))
            response = await client.send(upstream, stream=True)

            if not response.is_redirect:
                break

            location = response.headers.get("location")
            source_url = str(response.request.url)
            await response.aclose()
            response = None

            if not location:
                raise HTTPException(status_code=502, detail="上游跳转缺少 location")
            current_url = urljoin(source_url, location)
            if not _is_allowed(current_url):
                raise HTTPException(status_code=400, detail="跳转目标不在允许范围内")
        else:
            raise HTTPException(status_code=502, detail="上游跳转次数过多")

        assert response is not None
        if response.status_code >= 400:
            status_code = response.status_code
            await response.aclose()
            raise HTTPException(status_code=status_code, detail="上游返回错误")

        declared_length = response.headers.get("content-length")
        if declared_length and declared_length.isdigit() and int(declared_length) > MAX_MEDIA_BYTES:
            await response.aclose()
            raise HTTPException(status_code=413, detail="媒体超过大小上限")

        headers = {
            name: response.headers[name] for name in PASSTHROUGH_HEADERS if name in response.headers
        }
        media_type = response.headers.get("content-type", "application/octet-stream")
        upstream_response = response
        response = None  # 所有权转交给流式生成器

        async def stream_body():
            streamed = 0
            try:
                async for chunk in upstream_response.aiter_bytes():
                    streamed += len(chunk)
                    if streamed > MAX_MEDIA_BYTES:
                        break
                    yield chunk
            finally:
                await upstream_response.aclose()
                await client.aclose()

        return StreamingResponse(
            stream_body(),
            media_type=media_type,
            headers=headers,
            status_code=upstream_response.status_code,
        )
    except BaseException:
        if response is not None:
            await response.aclose()
        await client.aclose()
        raise


@router.get("/api/v2/media/proxy", tags=["媒体"], summary="媒体代理")
async def proxy_image(url: str, request: Request) -> StreamingResponse:
    return await proxy_media(url, request)

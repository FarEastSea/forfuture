"""媒体下载。内容哈希去重；失败不覆盖已有本地文件。"""
from __future__ import annotations

from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.settings_store import settings_store
from app.domain.enums import MediaKind, MediaStatus, Platform
from app.domain.media import MediaAsset
from app.media.storage import disk_path, guess_ext, hashed_filename, public_path, subdir_for

logger = get_logger("app.media")

QQ_SUFFIXES = ("qq.com", "qpic.cn", "qlogo.cn", "gtimg.cn", "gtimg.com")
XHS_SUFFIXES = ("xiaohongshu.com", "xhscdn.com")


def _allowed(url: str, platform: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    suffixes = QQ_SUFFIXES if platform == Platform.QQ.value else XHS_SUFFIXES
    return any(host == s or host.endswith(f".{s}") for s in suffixes)


def _headers(url: str, platform: str, *, video: bool = False) -> dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        )
    }
    if platform == Platform.XHS.value:
        headers.update(
            {
                "Origin": "https://www.xiaohongshu.com",
                "Referer": "https://www.xiaohongshu.com/",
                "Accept": "*/*" if video else "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            }
        )
    else:
        headers["Referer"] = "https://user.qzone.qq.com/"
    return headers


async def download_asset(
    session: AsyncSession,
    *,
    platform: str,
    kind: str,
    urls: list[str],
    existing_local_path: str | None = None,
    overwrite: bool = False,
) -> MediaAsset | None:
    urls = [u for u in urls if u]
    if not urls:
        return None

    if existing_local_path and not overwrite:
        path = disk_path(existing_local_path)
        if path.is_file():
            existing = (
                await session.execute(
                    select(MediaAsset).where(MediaAsset.local_path == existing_local_path)
                )
            ).scalar_one_or_none()
            if existing:
                return existing

    cfg = await settings_store.load_all(session)
    if not cfg.get("media_download_enabled", True):
        return await _register(session, platform, kind, urls[0], None, MediaStatus.REMOTE_ONLY)

    max_bytes = int(cfg.get("media_max_video_mb" if kind == MediaKind.VIDEO.value else "media_max_image_mb", 15))
    max_bytes *= 1024 * 1024
    content, content_type, used_url = await _fetch_first(urls, platform, kind == MediaKind.VIDEO.value, max_bytes)
    if content is None:
        status = MediaStatus.MISSING if existing_local_path else MediaStatus.FAILED
        return await _register(session, platform, kind, urls[0], existing_local_path, status, fallback=urls[1:])

    digest = __import__("hashlib").sha256(content).hexdigest()
    existing_hash = (
        await session.execute(select(MediaAsset).where(MediaAsset.content_sha256 == digest))
    ).scalar_one_or_none()
    if existing_hash and existing_hash.local_path:
        return existing_hash

    ext = guess_ext(used_url or urls[0], content_type)
    subdir = subdir_for(platform, kind)
    filename = hashed_filename(content, ext)
    dest = disk_path(public_path(subdir, filename))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    local = public_path(subdir, filename)
    return await _register(
        session,
        platform,
        kind,
        used_url or urls[0],
        local,
        MediaStatus.DOWNLOADED,
        sha256=digest,
        size=len(content),
        fallback=urls[1:],
    )


async def _register(
    session: AsyncSession,
    platform: str,
    kind: str,
    remote_url: str,
    local_path: str | None,
    status: MediaStatus,
    *,
    sha256: str | None = None,
    size: int | None = None,
    fallback: list[str] | None = None,
) -> MediaAsset:
    if local_path:
        existing = (
            await session.execute(select(MediaAsset).where(MediaAsset.local_path == local_path))
        ).scalar_one_or_none()
        if existing:
            existing.status = status.value
            existing.content_sha256 = sha256 or existing.content_sha256
            existing.byte_size = size or existing.byte_size
            return existing
    asset = MediaAsset(
        kind=kind,
        platform=platform,
        remote_url=remote_url,
        local_path=local_path,
        content_sha256=sha256,
        byte_size=size,
        status=status.value,
        fallback_urls=fallback or [],
    )
    session.add(asset)
    await session.flush()
    return asset


async def _fetch_first(
    urls: list[str], platform: str, video: bool, max_bytes: int
) -> tuple[bytes | None, str | None, str | None]:
    for url in urls:
        if url.startswith("/static/"):
            path = disk_path(url)
            if path.is_file():
                return path.read_bytes(), None, url
            continue
        if not _allowed(url, platform):
            continue
        try:
            content, content_type = await _http_get(url, platform, video, max_bytes)
            if content and len(content) >= (1000 if video else 100):
                return content, content_type, url
        except Exception as exc:
            logger.warning("媒体下载失败 %s: %s", url[:80], exc)
    return None, None, None


async def _http_get(url: str, platform: str, video: bool, max_bytes: int) -> tuple[bytes, str | None]:
    headers = _headers(url, platform, video=video)
    async with httpx.AsyncClient(timeout=45, follow_redirects=False, headers=headers) as client:
        current = url
        for _ in range(5):
            async with client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location") or ""
                    nxt = urljoin(current, location)
                    if not _allowed(nxt, platform):
                        raise ValueError("redirect not allowed")
                    current = nxt
                    continue
                response.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("media too large")
                    chunks.append(chunk)
                return b"".join(chunks), response.headers.get("content-type")
    raise ValueError("too many redirects")

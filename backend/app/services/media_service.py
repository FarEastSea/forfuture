import httpx
import os
import re
import ssl
import uuid
import asyncio
import logging
from urllib.parse import urljoin, urlparse
from pathlib import Path
from app.config import settings

logger = logging.getLogger(__name__)


class MediaService:
    QQ_MEDIA_SUFFIXES = ("qq.com", "qpic.cn", "qlogo.cn", "gtimg.cn", "gtimg.com")
    XHS_MEDIA_SUFFIXES = ("xiaohongshu.com", "xhscdn.com")
    MAX_IMAGE_BYTES = 15 * 1024 * 1024
    MAX_VIDEO_BYTES = 300 * 1024 * 1024

    def __init__(self):
        self.qq_dir = os.path.join(settings.static_dir, "qq_images")
        self.xhs_dir = os.path.join(settings.static_dir, "xhs_images")
        self.avatar_dir = os.path.join(settings.static_dir, "avatars")
        self.qq_video_dir = os.path.join(settings.static_dir, "qq_videos")
        self.xhs_video_dir = os.path.join(settings.static_dir, "xhs_videos")
        os.makedirs(self.qq_dir, exist_ok=True)
        os.makedirs(self.xhs_dir, exist_ok=True)
        os.makedirs(self.avatar_dir, exist_ok=True)
        os.makedirs(self.qq_video_dir, exist_ok=True)
        os.makedirs(self.xhs_video_dir, exist_ok=True)

    def _build_qq_cdn_fallbacks(self, url: str) -> list[str]:
        """为QQ CDN URL生成备选下载地址列表
        使用原始URL（QQ API返回的域名如 m.qpic.cn 等才是真实可访问的）
        """
        return [url]

    def _build_headers(self, url: str, platform: str) -> dict:
        """根据平台和URL构建完整的下载请求头"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0",
        }
        if platform == "xhs" or "xhscdn" in url or "xiaohongshu.com" in url:
            headers.update({
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Origin": "https://www.xiaohongshu.com",
                "Referer": "https://www.xiaohongshu.com/",
                "Sec-Fetch-Dest": "image",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "cross-site",
                "Sec-Ch-Ua": '"Not:A-Brand";v="99", "Microsoft Edge";v="145", "Chromium";v="145"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
            })
        elif platform == "qq" or "qzone" in url or "qpic" in url or "qq.com" in url:
            headers["Referer"] = "https://user.qzone.qq.com/"
        return headers

    def _build_video_headers(self, url: str, platform: str) -> dict:
        """构建视频下载请求头"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0",
        }
        if platform == "xhs" or "xhscdn" in url or "xiaohongshu.com" in url:
            headers.update({
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Origin": "https://www.xiaohongshu.com",
                "Referer": "https://www.xiaohongshu.com/",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "cross-site",
                "Sec-Ch-Ua": '"Not:A-Brand";v="99", "Microsoft Edge";v="145", "Chromium";v="145"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
            })
        elif platform == "qq" or "qq.com" in url:
            headers["Referer"] = "https://user.qzone.qq.com/"
        return headers

    def _allowed_suffixes_for_platform(self, platform: str) -> tuple[str, ...]:
        if platform == "qq":
            return self.QQ_MEDIA_SUFFIXES
        if platform == "xhs":
            return self.XHS_MEDIA_SUFFIXES
        return self.QQ_MEDIA_SUFFIXES + self.XHS_MEDIA_SUFFIXES

    def _is_allowed_media_url(self, url: str, platform: str) -> bool:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not hostname:
            return False
        return any(
            hostname == suffix or hostname.endswith(f".{suffix}")
            for suffix in self._allowed_suffixes_for_platform(platform)
        )

    async def _try_download_one(self, url: str, headers: dict, filepath: str, platform: str, retries: int = 3) -> str | None:
        """尝试下载单个URL到文件。成功返回实际文件路径，失败返回None。"""
        if not self._is_allowed_media_url(url, platform):
            logger.warning(f"图片下载被拒绝，域名不在允许列表: {url[:100]}")
            return None
        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(timeout=30, follow_redirects=False, headers=headers) as client:
                    current_url = url
                    for _ in range(5):
                        async with client.stream("GET", current_url) as resp:
                            if resp.status_code in (301, 302, 303, 307, 308):
                                location = resp.headers.get("location", "")
                                next_url = urljoin(current_url, location)
                                if not next_url or not self._is_allowed_media_url(next_url, platform):
                                    logger.warning(f"图片下载重定向到非允许域名: {next_url[:100]}")
                                    return None
                                current_url = next_url
                                continue

                            content_length = resp.headers.get("content-length")
                            if content_length:
                                try:
                                    content_size = int(content_length)
                                except ValueError:
                                    content_size = 0
                                if content_size > self.MAX_IMAGE_BYTES:
                                    logger.warning(f"图片下载超过大小限制: {current_url[:80]}, size={content_size:,}")
                                    return None

                            if resp.status_code == 200:
                                total_size = 0
                                with open(filepath, "wb") as f:
                                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                                        f.write(chunk)
                                        total_size += len(chunk)
                                        if total_size > self.MAX_IMAGE_BYTES:
                                            break
                                if total_size > self.MAX_IMAGE_BYTES:
                                    try:
                                        os.remove(filepath)
                                    except Exception:
                                        pass
                                    logger.warning(f"图片下载超过大小限制，中止: {current_url[:80]}")
                                    return None
                                if total_size > 100:
                                    logger.debug(f"图片下载成功: {current_url[:80]} → {os.path.basename(filepath)} ({total_size:,} bytes)")
                                    return filepath
                                try:
                                    os.remove(filepath)
                                except Exception:
                                    pass
                            logger.debug(f"图片下载响应异常: {current_url[:80]}, status={resp.status_code}")
                            break
            except Exception as e:
                logger.debug(f"图片下载请求失败(尝试{attempt+1}/{retries}): {url[:80]}: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(1 * (attempt + 1))
        return None

    async def download_image(self, url: str, platform: str = "qq") -> str | None:
        """下载图片到本地，返回相对路径。保留原始格式。"""
        if not url or url.startswith("/static/"):
            return url
        if not self._is_allowed_media_url(url, platform):
            logger.warning(f"图片URL域名不在允许列表，跳过下载: {url[:100]}")
            return None
        try:
            ext = self._guess_ext(url)
            filename = f"{uuid.uuid4().hex}{ext}"
            if platform == "qq":
                save_dir = self.qq_dir
                rel_prefix = "qq_images"
            elif platform == "xhs":
                save_dir = self.xhs_dir
                rel_prefix = "xhs_images"
            else:
                save_dir = self.avatar_dir
                rel_prefix = "avatars"

            filepath = os.path.join(save_dir, filename)
            headers = self._build_headers(url, platform)

            # 对QQ CDN图片：构建备选URL列表（通用CDN r.photo.store.qq.com 优先）
            urls_to_try = self._build_qq_cdn_fallbacks(url) if "photo.store.qq.com" in url else [url]

            for try_url in urls_to_try:
                actual_path = await self._try_download_one(try_url, headers, filepath, platform)
                if actual_path:
                    actual_filename = os.path.basename(actual_path)
                    return f"/static/{rel_prefix}/{actual_filename}"

            logger.warning(f"图片所有CDN均下载失败: {url[:100]}")
            return None
        except Exception as e:
            logger.error(f"下载图片失败 {url[:80]}: {e}")
            return None

    async def download_image_multi_url(self, urls: list[str], platform: str = "qq") -> str | None:
        """尝试多个URL下载同一张图片（如url1/url2/url3），返回第一个成功的本地路径"""
        for url in urls:
            result = await self.download_image(url, platform)
            if result:
                return result
        return None

    async def download_images(self, urls: list[str], platform: str = "qq") -> list[dict]:
        """批量下载图片"""
        results = []
        for url in urls:
            local_path = await self.download_image(url, platform)
            results.append({"url": url, "local_path": local_path})
        return results

    def _guess_ext(self, url: str) -> str:
        """从URL猜测图片扩展名，支持XHS CDN的!后缀格式"""
        path = url.split("?")[0].lower()
        for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif"]:
            if path.endswith(ext):
                return ext
        # XHS CDN URL: .../image_id!nd_dft_xxx_webp_3
        if "!" in path:
            suffix = path.split("!")[-1]
            if "webp" in suffix:
                return ".webp"
            if "jpg" in suffix or "jpeg" in suffix:
                return ".jpg"
            if "png" in suffix:
                return ".png"
        return ".jpg"

    async def download_video(self, url: str, platform: str = "xhs") -> str | None:
        """下载视频到本地，返回相对路径。支持大文件流式下载。"""
        if not url or url.startswith("/static/"):
            return url
        if not self._is_allowed_media_url(url, platform):
            logger.warning(f"视频URL域名不在允许列表，跳过下载: {url[:100]}")
            return None
        try:
            ext = self._guess_video_ext(url)
            filename = f"{uuid.uuid4().hex}{ext}"
            if platform == "qq":
                save_dir = self.qq_video_dir
                rel_prefix = "qq_videos"
            else:
                save_dir = self.xhs_video_dir
                rel_prefix = "xhs_videos"

            filepath = os.path.join(save_dir, filename)
            headers = self._build_video_headers(url, platform)

            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(timeout=120, follow_redirects=False, headers=headers) as client:
                        current_url = url
                        for _ in range(5):
                            async with client.stream("GET", current_url) as resp:
                                if resp.status_code in (301, 302, 303, 307, 308):
                                    location = resp.headers.get("location", "")
                                    next_url = urljoin(current_url, location)
                                    if not next_url or not self._is_allowed_media_url(next_url, platform):
                                        logger.warning(f"视频下载重定向到非允许域名: {next_url[:100]}")
                                        break
                                    current_url = next_url
                                    continue
                                if not self._is_allowed_media_url(str(resp.url), platform):
                                    logger.warning(f"视频下载重定向到非允许域名: {str(resp.url)[:100]}")
                                    break
                                if resp.status_code not in (200, 206):
                                    logger.debug(f"视频下载响应异常: {current_url[:80]}, status={resp.status_code}")
                                    break
                                content_length = resp.headers.get("content-length")
                                if content_length:
                                    try:
                                        content_size = int(content_length)
                                    except ValueError:
                                        content_size = 0
                                    if content_size > self.MAX_VIDEO_BYTES:
                                        logger.warning(f"视频下载超过大小限制: {current_url[:80]}, size={content_size:,}")
                                        break

                                total_size = 0
                                with open(filepath, "wb") as f:
                                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                                        f.write(chunk)
                                        total_size += len(chunk)
                                        if total_size > self.MAX_VIDEO_BYTES:
                                            logger.warning(f"视频下载超过大小限制，中止: {current_url[:80]}")
                                            break
                                if total_size > self.MAX_VIDEO_BYTES:
                                    try:
                                        os.remove(filepath)
                                    except Exception:
                                        pass
                                    break
                                if total_size > 1000:
                                    logger.info(f"视频下载成功: {current_url[:60]} → {filename} ({total_size:,} bytes)")
                                    return f"/static/{rel_prefix}/{filename}"
                                logger.debug(f"视频文件过小({total_size}B)，可能无效: {current_url[:80]}")
                                try:
                                    os.remove(filepath)
                                except Exception:
                                    pass
                                break
                except Exception as e:
                    logger.debug(f"视频下载失败(尝试{attempt+1}/3): {url[:80]}: {e}")
                if attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))

            logger.warning(f"视频下载全部重试失败: {url[:100]}")
            return None
        except Exception as e:
            logger.error(f"下载视频异常 {url[:80]}: {e}")
            return None

    def _guess_video_ext(self, url: str) -> str:
        path = url.split("?")[0].lower()
        for ext in [".mp4", ".mov", ".webm", ".mkv", ".avi", ".flv"]:
            if path.endswith(ext):
                return ext
        return ".mp4"


media_service = MediaService()

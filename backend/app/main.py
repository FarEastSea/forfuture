from urllib.parse import urljoin, urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os
import logging

from app.config import settings
from app.paths import FRONTEND_DIST
from app.routers import qq, xhs, auth, ai_chat, ai_tasks, ai_config, ws, system
from app.utils.log_handler import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化日志系统
    setup_logging()
    logger = logging.getLogger("app")
    logger.info(f"后端启动中... 监听 {settings.backend_host}:{settings.backend_port}")
    logger.info(f"数据库: {settings.database_host}:{settings.database_port}/{settings.database_name}")

    from app.services.task_scheduler import scheduler_service
    scheduler_service.start()
    logger.info("定时任务调度器已启动")
    yield
    # 关闭时
    scheduler_service.shutdown()


app = FastAPI(title="AI Records & Reminders", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
os.makedirs(settings.static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

# 注册路由
app.include_router(qq.router, prefix="/api/qq", tags=["QQ空间"])
app.include_router(xhs.router, prefix="/api/xhs", tags=["小红书"])
app.include_router(auth.router, prefix="/api/auth", tags=["登录管理"])
app.include_router(ai_chat.router, prefix="/api/chat", tags=["AI对话"])
app.include_router(ai_tasks.router, prefix="/api/tasks", tags=["定时任务"])
app.include_router(ai_config.router, prefix="/api/ai-config", tags=["AI配置"])
app.include_router(system.router, prefix="/api/system", tags=["系统配置"])
app.include_router(ws.router, tags=["WebSocket"])


@app.get("/api/health")
async def health():
    return {"status": "ok"}


QQ_PROXY_HOST_SUFFIXES = (
    "qq.com",
    "qpic.cn",
    "qlogo.cn",
    "gtimg.cn",
    "gtimg.com",
)

XHS_PROXY_HOST_SUFFIXES = (
    "xiaohongshu.com",
    "xhscdn.com",
)

MAX_PROXY_REDIRECTS = 5


def _is_allowed_proxy_host(hostname: str, allowed_suffixes: tuple[str, ...]) -> bool:
    return any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in allowed_suffixes)


def _is_allowed_proxy_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not hostname:
        return False

    return _is_allowed_proxy_host(hostname, QQ_PROXY_HOST_SUFFIXES + XHS_PROXY_HOST_SUFFIXES)


def _build_proxy_headers(url: str, range_header: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    hostname = (urlparse(url).hostname or "").lower()

    if _is_allowed_proxy_host(hostname, QQ_PROXY_HOST_SUFFIXES):
        headers["Referer"] = "https://user.qzone.qq.com/"

    if _is_allowed_proxy_host(hostname, XHS_PROXY_HOST_SUFFIXES):
        headers.update({
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://www.xiaohongshu.com",
            "Referer": "https://www.xiaohongshu.com/",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        })

    if range_header:
        headers["Range"] = range_header

    return headers


@app.get("/api/proxy/image")
async def proxy_image(url: str, request: Request):
    """代理外部媒体资源（图片/视频），为 QQ / 小红书 CDN 补充必要请求头。"""
    import httpx
    from fastapi.responses import StreamingResponse

    if not _is_allowed_proxy_url(url):
        raise HTTPException(status_code=400, detail="Invalid URL")

    client = httpx.AsyncClient(timeout=30, follow_redirects=False)
    response = None
    try:
        range_header = request.headers.get("range")
        current_url = url
        redirect_count = 0

        while True:
            headers = _build_proxy_headers(current_url, range_header=range_header)
            upstream_request = client.build_request("GET", current_url, headers=headers)
            response = await client.send(upstream_request, stream=True)

            if response.is_redirect:
                redirect_target = response.headers.get("location")
                source_url = str(response.request.url)
                await response.aclose()
                response = None

                if not redirect_target:
                    await client.aclose()
                    raise HTTPException(status_code=502, detail="Upstream redirect missing location")

                redirect_count += 1
                if redirect_count > MAX_PROXY_REDIRECTS:
                    await client.aclose()
                    raise HTTPException(status_code=502, detail="Too many upstream redirects")

                current_url = urljoin(source_url, redirect_target)
                if not _is_allowed_proxy_url(current_url):
                    await client.aclose()
                    raise HTTPException(status_code=400, detail="Redirect target not allowed")
                continue

            break

        if response.status_code >= 400:
            await response.aclose()
            await client.aclose()
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch upstream media")

        passthrough_headers = {}
        for header_name in ("accept-ranges", "content-length", "content-range", "cache-control"):
            header_value = response.headers.get(header_name)
            if header_value:
                passthrough_headers[header_name] = header_value

        content_type = response.headers.get("content-type", "application/octet-stream")

        async def iter_media():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return StreamingResponse(
            iter_media(),
            media_type=content_type,
            headers=passthrough_headers,
            status_code=response.status_code,
        )
    except HTTPException:
        raise
    except Exception as exc:
        if response is not None:
            await response.aclose()
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Failed to fetch media: {exc}") from exc

# 挂载前端dist（放在API路由之后，作为fallback）
_frontend_dist = os.fspath(FRONTEND_DIST)
if os.path.isdir(_frontend_dist):
    logging.getLogger("app").info(f"前端dist已挂载: {_frontend_dist}")
    # 先挂载 assets 子目录（JS/CSS等静态资源）
    _assets_dir = os.path.join(_frontend_dist, "assets")
    if os.path.isdir(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="frontend_assets")
    # SPA fallback：所有非API/WS/static路径返回index.html
    from fastapi.responses import FileResponse

    @app.get("/")
    async def serve_spa_root():
        return FileResponse(os.path.join(_frontend_dist, "index.html"))

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # 如果请求的是具体静态文件且存在，直接返回
        file_path = os.path.join(_frontend_dist, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        # 否则返回 index.html（SPA路由交给前端处理）
        return FileResponse(os.path.join(_frontend_dist, "index.html"))
else:
    logging.getLogger("app").warning(f"前端dist未找到: {_frontend_dist}")

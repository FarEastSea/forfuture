from fastapi import FastAPI
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


@app.get("/api/proxy/image")
async def proxy_image(url: str):
    """代理外部图片（解决QQ CDN等需要Referer/Cookie的图片访问问题）"""
    import httpx
    from fastapi.responses import Response
    if not url or not url.startswith("http"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid URL")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        if "qq.com" in url or "qpic" in url or "qzone" in url:
            headers["Referer"] = "https://user.qzone.qq.com/"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, verify=False, headers=headers) as client:
            resp = await client.get(url)
            content_type = resp.headers.get("content-type", "image/jpeg")
            return Response(content=resp.content, media_type=content_type)
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(status_code=502, detail="Failed to fetch image")

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

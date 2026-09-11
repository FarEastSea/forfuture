"""ASGI 应用装配。

对外只提供 v2 API、稳定的 NapCat/事件 WebSocket 与静态资源。legacy 代码暂时
仅作为扫码登录的内部实现保留，不再直接暴露旧 HTTP/WebSocket 路由。
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import media_proxy, napcat
from app.api.v2 import router as v2_router
from app.api.ws import router as events_ws_router
from app.core.config import settings
from app.core.errors import AppError
from app.core.events import event_bus
from app.core.logging import get_logger, setup_logging
from app.core.paths import FRONTEND_DIST

logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    settings.ensure_directories()
    logger.info("后端启动: %s:%s", settings.backend_host, settings.backend_port)
    logger.info("静态目录: %s", settings.static_path)
    logger.info("数据库: %s:%s/%s", settings.database_host, settings.database_port, settings.database_name)
    if not settings.has_strong_admin_token:
        logger.warning("未配置 ADMIN_API_TOKEN 且 SECRET_KEY 为默认值，管理接口将全部拒绝访问")

    await event_bus.start()
    from app.platforms.registry import load_builtin_platforms

    load_builtin_platforms()
    from app.scheduler import task_scheduler

    await task_scheduler.start()

    try:
        yield
    finally:
        task_scheduler.shutdown()
        await event_bus.stop()


def create_app() -> FastAPI:
    application = FastAPI(
        title="AI Records & Reminders",
        version="2.0.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @application.get("/api/health", tags=["系统"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": application.version}

    application.include_router(v2_router, prefix="/api/v2")
    application.include_router(media_proxy.router)
    application.include_router(napcat.router)
    application.include_router(events_ws_router)

    settings.ensure_directories()
    application.mount("/static", StaticFiles(directory=settings.static_path), name="static")

    _mount_frontend(application)
    return application


def _mount_frontend(application: FastAPI) -> None:
    """挂载前端 dist，并为 SPA 路由提供 index.html 兜底。必须最后注册。"""
    dist = os.fspath(FRONTEND_DIST)
    if not os.path.isdir(dist):
        logger.warning("前端 dist 未找到，仅提供 API/WS/static: %s", dist)
        return

    logger.info("前端 dist 已挂载: %s", dist)
    assets_dir = os.path.join(dist, "assets")
    if os.path.isdir(assets_dir):
        application.mount("/assets", StaticFiles(directory=assets_dir), name="frontend_assets")

    index_file = os.path.join(dist, "index.html")

    @application.get("/", include_in_schema=False)
    async def serve_root() -> FileResponse:
        return FileResponse(index_file)

    @application.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> FileResponse:
        candidate = os.path.join(dist, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(index_file)


app = create_app()

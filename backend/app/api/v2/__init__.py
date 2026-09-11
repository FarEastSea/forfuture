"""v2 API 路由聚合。"""
from fastapi import APIRouter

from app.api.v2 import accounts, agent, auth, content, crawl, knowledge, providers, system, tasks

router = APIRouter()
router.include_router(system.router, prefix="/system", tags=["系统"])
router.include_router(content.router, prefix="/content", tags=["内容"])
router.include_router(accounts.router, prefix="/accounts", tags=["账号"])
router.include_router(auth.router, prefix="/auth", tags=["登录"])
router.include_router(crawl.router, prefix="/crawl", tags=["采集"])
router.include_router(knowledge.router, prefix="/knowledge", tags=["知识库"])
router.include_router(agent.router, prefix="/agent", tags=["Agent"])
router.include_router(tasks.router, prefix="/tasks", tags=["定时任务"])
router.include_router(providers.router, prefix="/providers", tags=["AI 配置"])

__all__ = ["router"]

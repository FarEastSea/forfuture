from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.system_config import SystemConfig
from app.schemas import SystemConfigItem
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/configs")
async def get_system_configs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemConfig).order_by(SystemConfig.key))
    configs = result.scalars().all()
    return [
        {
            "id": c.id, "key": c.key, "value": c.value,
            "description": c.description,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in configs
    ]


@router.put("/configs")
async def update_system_configs(items: List[SystemConfigItem] = Body(...), db: AsyncSession = Depends(get_db)):
    try:
        for item in items:
            result = await db.execute(select(SystemConfig).where(SystemConfig.key == item.key))
            config = result.scalar_one_or_none()
            if config:
                config.value = item.value
                if item.description:
                    config.description = item.description
            else:
                config = SystemConfig(key=item.key, value=item.value, description=item.description)
                db.add(config)
        await db.commit()
        logger.info(f"系统配置已更新: {[item.key for item in items]}")
        return {"message": "配置已更新"}
    except Exception as e:
        await db.rollback()
        logger.error(f"保存系统配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")


@router.get("/configs/{key}")
async def get_system_config(key: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置项不存在")
    return {"key": config.key, "value": config.value, "description": config.description}


@router.get("/napcat-status")
async def get_napcat_status():
    """获取当前 NapCat 连接状态"""
    from app.services.napcat_client import napcat_client
    status = napcat_client.get_status()
    # 添加额外调试信息
    from app.routers.ws import napcat_connections
    status["ws_connections_keys"] = list(napcat_connections.keys())
    status["frontend_clients_count"] = len(napcat_client.frontend_clients)
    return status


@router.get("/napcat-logs")
async def get_napcat_logs():
    """获取NapCat连接日志（最近20条）"""
    from app.services.napcat_client import napcat_client
    return {"logs": napcat_client.get_logs()}


@router.get("/db-config")
async def get_db_config():
    """获取当前数据库连接配置（脱敏）"""
    from app.config import settings
    return {
        "host": settings.database_host,
        "port": settings.database_port,
        "name": settings.database_name,
        "user": settings.database_user,
        "password": "***",
    }


@router.post("/db-config/test")
async def test_db_connection(host: str, port: int, name: str, user: str, password: str):
    """测试数据库连接"""
    import asyncpg
    try:
        conn = await asyncpg.connect(host=host, port=port, database=name, user=user, password=password)
        version = await conn.fetchval("SELECT version()")
        await conn.close()
        return {"success": True, "message": f"连接成功: {version[:60]}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ===== 统一日志系统 =====

@router.get("/logs")
async def get_system_logs(
    level: Optional[str] = Query(None, description="日志等级: debug/info/warning/error"),
    module: Optional[str] = Query(None, description="模块过滤: ws, qq_crawler, napcat_client 等"),
    keyword: Optional[str] = Query(None, description="关键词搜索"),
    limit: int = Query(200, ge=1, le=1000),
):
    """获取后端统一日志（内存中最近2000条）"""
    from app.utils.log_handler import get_logs, get_log_count
    logs = get_logs(level=level, module=module, keyword=keyword, limit=limit)
    return {"logs": logs, "total_buffered": get_log_count()}


@router.delete("/logs")
async def clear_system_logs():
    """清空内存日志"""
    from app.utils.log_handler import clear_logs
    clear_logs()
    return {"message": "日志已清空"}


# ===== 动态总结 =====

@router.get("/summary/stats")
async def get_summary_stats():
    """获取动态总结统计信息"""
    from app.services.summary_service import summary_service
    return await summary_service.get_summary_stats()


@router.post("/summary/generate")
async def generate_summaries(force_all: bool = Query(False, description="是否重新总结所有记录")):
    """手动触发动态总结生成"""
    from app.services.summary_service import summary_service
    try:
        stats = await summary_service.generate_summaries(force_all=force_all)
        return {"message": f"总结完成: 新增{stats['new']}条, 更新{stats['updated']}条", "stats": stats}
    except Exception as e:
        logger.error(f"手动总结失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"总结失败: {str(e)}")

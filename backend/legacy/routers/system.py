from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from legacy.database import get_db
from legacy.config import settings, ENV_FILE
from legacy.models.system_config import SystemConfig
from legacy.schemas import SystemConfigItem
from legacy.security import require_admin_http
from typing import List, Optional
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_admin_http)])

ENV_SYNC_KEYS = {
    "database_host",
    "database_port",
    "database_name",
    "database_user",
    "database_password",
    "napcat_ws_url",
    "napcat_token",
}

SENSITIVE_ENV_KEYS = {"database_password", "napcat_token"}

ENV_FILE_KEYS = {
    "database_host": "DATABASE_HOST",
    "database_port": "DATABASE_PORT",
    "database_name": "DATABASE_NAME",
    "database_user": "DATABASE_USER",
    "database_password": "DATABASE_PASSWORD",
    "napcat_ws_url": "NAPCAT_WS_URL",
    "napcat_token": "NAPCAT_TOKEN",
}

ENV_KEY_ALIASES = {
    **{key.lower(): key for key in ENV_SYNC_KEYS},
    **{env_key.lower(): key for key, env_key in ENV_FILE_KEYS.items()},
}


class DatabaseConnectionTestRequest(BaseModel):
    host: str
    port: int
    name: str
    user: str
    password: str


def _format_env_value(value: str) -> str:
    if value == "":
        return '""'
    if any(ch.isspace() for ch in value) or any(ch in value for ch in '#"\\'):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _load_env_settings() -> dict[str, str]:
    if not ENV_FILE.exists():
        return {}

    values: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue

        raw_key, _, raw_value = line.partition("=")
        key = ENV_KEY_ALIASES.get(raw_key.strip().lower(), raw_key.strip())
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        values[key] = value

    return values


def _sync_env_settings(updates: dict[str, str]) -> None:
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = ENV_FILE.read_text(encoding="utf-8").splitlines(keepends=True) if ENV_FILE.exists() else []
    rewritten_lines: list[str] = []
    seen_keys: set[str] = set()

    for line in existing_lines:
        if "=" not in line or line.lstrip().startswith("#"):
            rewritten_lines.append(line)
            continue

        raw_key, _, _ = line.partition("=")
        normalized_key = ENV_KEY_ALIASES.get(raw_key.strip().lower(), raw_key.strip())
        if normalized_key in updates:
            env_file_key = ENV_FILE_KEYS.get(normalized_key, normalized_key)
            rewritten_lines.append(f"{env_file_key}={_format_env_value(updates[normalized_key])}\n")
            seen_keys.add(normalized_key)
        else:
            rewritten_lines.append(line if line.endswith("\n") else f"{line}\n")

    for key, value in updates.items():
        if key not in seen_keys:
            env_file_key = ENV_FILE_KEYS.get(key, key)
            rewritten_lines.append(f"{env_file_key}={_format_env_value(value)}\n")

    temp_env_file = ENV_FILE.with_name(f"{ENV_FILE.name}.tmp")
    temp_env_file.write_text("".join(rewritten_lines), encoding="utf-8")
    temp_env_file.replace(ENV_FILE)


def _restore_env_snapshot(snapshot: str | None) -> None:
    if snapshot is None:
        if ENV_FILE.exists():
            ENV_FILE.unlink()
        return

    temp_env_file = ENV_FILE.with_name(f"{ENV_FILE.name}.tmp")
    temp_env_file.write_text(snapshot, encoding="utf-8")
    temp_env_file.replace(ENV_FILE)


async def _get_config_value(db: AsyncSession, key: str, default: str) -> str:
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    config = result.scalar_one_or_none()
    return config.value if config and config.value is not None else default


async def _sync_auto_crawl_schedule(db: AsyncSession) -> None:
    enabled_value = await _get_config_value(db, "auto_crawl_enabled", "true")
    interval_value = await _get_config_value(db, "auto_crawl_interval", "60")
    try:
        interval = max(1, int(interval_value))
    except (TypeError, ValueError):
        interval = 60

    from legacy.services.task_scheduler import scheduler_service
    scheduler_service.configure_auto_crawl(enabled_value != "false", interval)


@router.get("/configs")
async def get_system_configs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemConfig).order_by(SystemConfig.key))
    configs = result.scalars().all()
    serialized = [
        {
            "id": c.id,
            "key": c.key,
            "value": "***" if c.key in SENSITIVE_ENV_KEYS and c.value else c.value,
            "description": c.description,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in configs
    ]

    env_values = _load_env_settings()
    config_map = {item["key"]: item for item in serialized}
    for key in ENV_SYNC_KEYS:
        if key in env_values:
            serialized_value = "***" if key in SENSITIVE_ENV_KEYS and env_values[key] else env_values[key]
            if key in config_map:
                config_map[key]["value"] = serialized_value
            else:
                config_map[key] = {
                    "id": None,
                    "key": key,
                    "value": serialized_value,
                    "description": None,
                    "updated_at": None,
                }

    return sorted(config_map.values(), key=lambda item: item["key"])


@router.put("/configs")
async def update_system_configs(items: List[SystemConfigItem] = Body(...), db: AsyncSession = Depends(get_db)):
    env_updates: dict[str, str] = {}
    env_snapshot = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else None
    try:
        current_env_values = _load_env_settings()
        for item in items:
            env_value = item.value or ""
            stored_value = item.value
            result = await db.execute(select(SystemConfig).where(SystemConfig.key == item.key))
            config = result.scalar_one_or_none()
            if item.key in SENSITIVE_ENV_KEYS:
                current_secret = current_env_values.get(item.key)
                if not current_secret:
                    runtime_value = getattr(settings, item.key, "")
                    current_secret = str(runtime_value) if runtime_value else ""
                if not current_secret and config and config.value and config.value != "***":
                    current_secret = config.value
                if env_value in {"", "***"} and current_secret:
                    env_value = current_secret
                stored_value = "***" if env_value else ""

            if config:
                config.value = stored_value
                if item.description:
                    config.description = item.description
            else:
                config = SystemConfig(key=item.key, value=stored_value, description=item.description)
                db.add(config)
            if item.key in ENV_SYNC_KEYS:
                env_updates[item.key] = env_value

        if env_updates:
            _sync_env_settings(env_updates)

        await db.commit()

        if any(item.key in {"auto_crawl_enabled", "auto_crawl_interval"} for item in items):
            await _sync_auto_crawl_schedule(db)

        logger.info(f"系统配置已更新: {[item.key for item in items]}")
        return {"message": "配置已更新"}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        if env_updates:
            try:
                _restore_env_snapshot(env_snapshot)
            except Exception as restore_error:
                logger.error(f"恢复 .env 快照失败: {restore_error}", exc_info=True)
        logger.error(f"保存系统配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")


@router.get("/configs/{key}")
async def get_system_config(key: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="配置项不存在")
    value = "***" if key in SENSITIVE_ENV_KEYS and config.value else config.value
    return {"key": config.key, "value": value, "description": config.description}


@router.get("/napcat-status")
async def get_napcat_status():
    """获取当前 NapCat 连接状态"""
    from legacy.services.napcat_client import napcat_client
    status = napcat_client.get_status()
    # 添加额外调试信息
    from legacy.routers.ws import napcat_connections
    status["ws_connections_keys"] = list(napcat_connections.keys())
    status["frontend_clients_count"] = len(napcat_client.frontend_clients)
    return status


@router.get("/napcat-logs")
async def get_napcat_logs():
    """获取NapCat连接日志（最近20条）"""
    from legacy.services.napcat_client import napcat_client
    return {"logs": napcat_client.get_logs()}


@router.get("/db-config")
async def get_db_config(db: AsyncSession = Depends(get_db)):
    """获取已保存的数据库连接配置（脱敏，重启后生效）"""
    env_values = _load_env_settings()
    config_result = await db.execute(
        select(SystemConfig).where(
            SystemConfig.key.in_(
                [
                    "database_host",
                    "database_port",
                    "database_name",
                    "database_user",
                    "database_password",
                ]
            )
        )
    )
    db_values = {config.key: config.value for config in config_result.scalars().all()}

    return {
        "host": env_values.get("database_host") or db_values.get("database_host") or settings.database_host,
        "port": int(env_values.get("database_port") or db_values.get("database_port") or settings.database_port),
        "name": env_values.get("database_name") or db_values.get("database_name") or settings.database_name,
        "user": env_values.get("database_user") or db_values.get("database_user") or settings.database_user,
        "password": "***" if (env_values.get("database_password") or db_values.get("database_password") or settings.database_password) else "",
    }


@router.post("/db-config/test")
async def test_db_connection(req: DatabaseConnectionTestRequest):
    """测试数据库连接"""
    import asyncpg
    try:
        conn = await asyncpg.connect(
            host=req.host,
            port=req.port,
            database=req.name,
            user=req.user,
            password=req.password,
        )
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
    from legacy.utils.log_handler import get_logs, get_log_count
    logs = get_logs(level=level, module=module, keyword=keyword, limit=limit)
    return {"logs": logs, "total_buffered": get_log_count()}


@router.delete("/logs")
async def clear_system_logs():
    """清空内存日志"""
    from legacy.utils.log_handler import clear_logs
    clear_logs()
    return {"message": "日志已清空"}


# ===== 动态总结 =====

@router.get("/summary/stats")
async def get_summary_stats():
    """获取动态总结统计信息"""
    from legacy.services.summary_service import summary_service
    return await summary_service.get_summary_stats()


@router.post("/summary/generate")
async def generate_summaries(force_all: bool = Query(False, description="是否重新总结所有记录")):
    """手动触发动态总结生成"""
    from legacy.services.summary_service import summary_service
    try:
        stats = await summary_service.generate_summaries(force_all=force_all)
        return {"message": f"总结完成: 新增{stats['new']}条, 更新{stats['updated']}条", "stats": stats}
    except Exception as e:
        logger.error(f"手动总结失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"总结失败: {str(e)}")

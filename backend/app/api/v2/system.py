"""系统、运行期设置与基础设施配置接口。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import AdminDep, SessionDep
from app.core.config import ENV_FILE, settings
from app.core.settings_store import settings_store
from app.domain.content import ContentComment, ContentItem
from app.domain.identity import LoginCredential, PlatformAccount
from app.domain.knowledge import KnowledgeChunk
from app.domain.media import MediaAsset
from app.notifications.napcat import napcat_client

router = APIRouter()


class SettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class InfrastructureUpdate(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


class DatabaseConnectionTest(BaseModel):
    host: str
    port: int = Field(ge=1, le=65535)
    name: str
    user: str
    password: str = ""


class RedisConnectionTest(BaseModel):
    url: str = ""


_INFRASTRUCTURE_KEYS = {
    "database_host": "DATABASE_HOST",
    "database_port": "DATABASE_PORT",
    "database_name": "DATABASE_NAME",
    "database_user": "DATABASE_USER",
    "database_password": "DATABASE_PASSWORD",
    "napcat_ws_url": "NAPCAT_WS_URL",
    "napcat_token": "NAPCAT_TOKEN",
    "redis_url": "REDIS_URL",
    "redis_enabled": "REDIS_ENABLED",
}
_SENSITIVE_INFRASTRUCTURE_KEYS = {"database_password", "napcat_token", "redis_url"}


def _read_env() -> tuple[list[str], dict[str, str]]:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    values: dict[str, str] = {}
    reverse = {value: key for key, value in _INFRASTRUCTURE_KEYS.items()}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        raw_key, raw_value = line.split("=", 1)
        key = reverse.get(raw_key.strip().upper())
        if key:
            value = raw_value.strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            values[key] = value
    return lines, values


def _env_value(value: str) -> str:
    if value == "":
        return '""'
    if any(char.isspace() or char in '#"\\' for char in value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _write_infrastructure(updates: dict[str, str]) -> None:
    lines, _ = _read_env()
    pending = dict(updates)
    rewritten: list[str] = []
    reverse = {value: key for key, value in _INFRASTRUCTURE_KEYS.items()}
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            rewritten.append(line)
            continue
        raw_key = line.split("=", 1)[0].strip().upper()
        logical_key = reverse.get(raw_key)
        if logical_key not in pending:
            rewritten.append(line)
            continue
        rewritten.append(f"{_INFRASTRUCTURE_KEYS[logical_key]}={_env_value(pending.pop(logical_key))}")
    for key, value in pending.items():
        rewritten.append(f"{_INFRASTRUCTURE_KEYS[key]}={_env_value(value)}")
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = ENV_FILE.with_name(f"{ENV_FILE.name}.tmp")
    temporary.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    temporary.replace(ENV_FILE)


def _redact_redis_url(value: str) -> str:
    """保留主机、端口与库号，只遮蔽 Redis URL 中的凭据。"""
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "***"
    hostname = parsed.hostname or ""
    if not hostname:
        return "***"
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = f":{parsed.port}" if parsed.port else ""
    if parsed.username is not None or parsed.password is not None:
        username = parsed.username or ""
        auth = f"{username}:***@" if username else ":***@"
    else:
        auth = ""
    return urlunsplit((parsed.scheme, f"{auth}{host}{port}", parsed.path, parsed.query, parsed.fragment))


@router.get("/settings", summary="读取全部运行期设置")
async def read_settings(session: SessionDep, _: AdminDep) -> dict[str, Any]:
    _, infrastructure = _read_env()
    for key in _SENSITIVE_INFRASTRUCTURE_KEYS:
        if infrastructure.get(key):
            infrastructure[key] = "***"
    return {
        "values": {**(await settings_store.load_all(session)), **infrastructure},
        "specs": settings_store.describe(),
    }


@router.put("/settings", summary="更新运行期设置")
async def update_settings(payload: SettingsUpdate, session: SessionDep, _: AdminDep) -> dict[str, Any]:
    values = await settings_store.set_many(session, payload.values)
    await session.commit()
    return {"values": values}


@router.put("/infrastructure", summary="更新重启后生效的基础设施配置")
async def update_infrastructure(payload: InfrastructureUpdate, _: AdminDep) -> dict[str, Any]:
    unknown = sorted(set(payload.values) - set(_INFRASTRUCTURE_KEYS))
    if unknown:
        return {"updated": [], "ignored": unknown}
    _, current = _read_env()
    updates: dict[str, str] = {}
    for key, raw_value in payload.values.items():
        value = str(raw_value)
        if key in _SENSITIVE_INFRASTRUCTURE_KEYS and (value in {"", "***"} or "***" in value) and current.get(key):
            value = current[key]
        updates[key] = value
    if updates:
        _write_infrastructure(updates)
    return {"updated": sorted(updates), "restart_required": bool(updates)}


@router.get("/stats", summary="内容与媒体统计")
async def read_stats(session: SessionDep, _: AdminDep) -> dict[str, Any]:
    async def count(stmt) -> int:
        return int((await session.execute(stmt)).scalar_one())

    by_platform = (
        await session.execute(
            select(ContentItem.platform, func.count()).group_by(ContentItem.platform)
        )
    ).all()

    return {
        "content_items": await count(select(func.count()).select_from(ContentItem)),
        "content_by_platform": {platform: total for platform, total in by_platform},
        "comments": await count(select(func.count()).select_from(ContentComment)),
        "media_assets": await count(select(func.count()).select_from(MediaAsset)),
        "knowledge_chunks": await count(select(func.count()).select_from(KnowledgeChunk)),
        "targets": await count(select(func.count()).select_from(PlatformAccount)),
        "credentials": await count(select(func.count()).select_from(LoginCredential)),
    }


@router.get("/runtime", summary="运行环境信息")
async def read_runtime(_: AdminDep) -> dict[str, Any]:
    return {
        "static_path": str(settings.static_path),
        "log_path": str(settings.log_path),
        "browser_profile_path": str(settings.browser_profile_path),
        "browser_channel": settings.browser_channel,
        "redis_enabled": settings.redis_enabled,
        "embedding_dimensions": settings.embedding_dimensions,
    }


@router.get("/napcat-status", summary="NapCat 连接状态")
async def read_napcat_status(_: AdminDep) -> dict[str, Any]:
    return napcat_client.get_status()


@router.get("/database", summary="读取脱敏数据库配置")
async def read_database_config(_: AdminDep) -> dict[str, Any]:
    _, values = _read_env()
    return {
        "host": values.get("database_host", settings.database_host),
        "port": int(values.get("database_port", settings.database_port)),
        "name": values.get("database_name", settings.database_name),
        "user": values.get("database_user", settings.database_user),
        "password": "***" if values.get("database_password", settings.database_password) else "",
    }


@router.post("/database/test", summary="测试数据库连接")
async def test_database_connection(payload: DatabaseConnectionTest, _: AdminDep) -> dict[str, Any]:
    import asyncpg

    password = payload.password
    if password in {"", "***"}:
        _, values = _read_env()
        password = values.get("database_password", settings.database_password)
    try:
        connection = await asyncpg.connect(
            host=payload.host,
            port=payload.port,
            database=payload.name,
            user=payload.user,
            password=password,
            timeout=10,
        )
        version = await connection.fetchval("SELECT version()")
        await connection.close()
        return {"success": True, "message": f"连接成功: {str(version)[:60]}"}
    except Exception as exc:
        return {"success": False, "message": f"连接失败: {type(exc).__name__}"}


@router.get("/redis", summary="读取脱敏 Redis 配置与状态")
async def read_redis_config(_: AdminDep) -> dict[str, Any]:
    _, values = _read_env()
    url = values.get("redis_url", settings.redis_url)
    enabled = values.get("redis_enabled", str(settings.redis_enabled)).strip().lower() not in {
        "0", "false", "no", "off",
    }
    return {
        "enabled": enabled,
        "display_url": _redact_redis_url(url),
        "configured": bool(url),
    }


@router.post("/redis/test", summary="测试 Redis 连接")
async def test_redis_connection(payload: RedisConnectionTest, _: AdminDep) -> dict[str, Any]:
    import redis.asyncio as redis_asyncio

    _, values = _read_env()
    url = payload.url.strip()
    if not url or "***" in url:
        url = values.get("redis_url", settings.redis_url)
    if not url:
        return {"success": False, "message": "尚未配置 Redis URL"}
    client = redis_asyncio.from_url(url, decode_responses=True)
    try:
        pong = await client.ping()
        return {"success": bool(pong), "message": "Redis 连接成功" if pong else "Redis 未返回 PONG"}
    except Exception as exc:
        return {"success": False, "message": f"Redis 连接失败: {type(exc).__name__}"}
    finally:
        await client.aclose()


def _log_files() -> list[Path]:
    primary = settings.log_path / "app.jsonl"
    return [path for path in [primary, *sorted(settings.log_path.glob("app.jsonl.*"), reverse=True)] if path.is_file()]


@router.get("/logs", summary="读取结构化日志")
async def read_logs(
    _: AdminDep,
    level: str | None = None,
    module: str | None = None,
    keyword: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for path in _log_files():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if level and str(item.get("level", "")).lower() != level.lower():
                continue
            if module and module.lower() not in str(item.get("logger", "")).lower():
                continue
            if keyword and keyword.lower() not in json.dumps(item, ensure_ascii=False).lower():
                continue
            rows.append(item)
    return {"logs": rows[-limit:], "total_buffered": len(rows)}


@router.delete("/logs", summary="清空当前日志")
async def clear_logs(_: AdminDep) -> dict[str, str]:
    primary = settings.log_path / "app.jsonl"
    primary.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text("", encoding="utf-8")
    return {"message": "日志已清空"}


@router.get("/summary/stats", summary="知识索引覆盖统计")
async def read_summary_stats(session: SessionDep, _: AdminDep) -> dict[str, Any]:
    total = int((await session.execute(select(func.count(ContentItem.id)))).scalar_one() or 0)
    summarized = int(
        (
            await session.execute(
                select(func.count(func.distinct(KnowledgeChunk.content_item_id)))
            )
        ).scalar_one()
        or 0
    )
    last_time = (await session.execute(select(func.max(KnowledgeChunk.created_at)))).scalar_one()
    return {
        "total": total,
        "summarized": summarized,
        "coverage": round(summarized * 100 / total, 1) if total else 100,
        "last_summary_time": last_time.isoformat() if isinstance(last_time, datetime) else None,
    }


@router.post("/summary/generate", summary="增量更新知识索引")
async def generate_summaries(
    session: SessionDep,
    _: AdminDep,
    force_all: bool = Query(False),
) -> dict[str, Any]:
    from app.knowledge.indexer import index_item, probe_embedding

    embedding_available, embedding_error = await probe_embedding(session)
    rows = (await session.execute(select(ContentItem).order_by(ContentItem.id))).scalars().all()
    chunks = 0
    for item in rows:
        chunks += await index_item(
            session,
            item,
            force=force_all,
            use_embeddings=embedding_available,
        )
    await session.commit()
    return {
        "message": f"知识索引更新完成：处理 {len(rows)} 条内容，写入 {chunks} 个分块",
        "stats": {"new": chunks, "updated": 0},
        "embedding_provider_available": embedding_available,
        "embedding_error": embedding_error,
    }

"""运行期设置：带类型定义的注册表，值存在 app_settings 表。

旧实现把设置散落在 system_configs 里，全是字符串，每个读取点各自解析、各写各的默认值。
这里集中声明每个键的类型、默认值和取值范围，读写都走注册表。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.domain.ops import AppSetting

SettingType = Literal["bool", "int", "float", "str", "json"]


@dataclass(frozen=True)
class SettingSpec:
    key: str
    type: SettingType
    default: Any
    description: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] | None = None
    secret: bool = False

    def coerce(self, value: Any) -> Any:
        if value is None:
            return self.default
        try:
            if self.type == "bool":
                if isinstance(value, str):
                    coerced: Any = value.strip().lower() in {"1", "true", "yes", "on"}
                else:
                    coerced = bool(value)
            elif self.type == "int":
                coerced = int(value)
            elif self.type == "float":
                coerced = float(value)
            elif self.type == "str":
                coerced = str(value)
            else:
                coerced = value
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"设置 {self.key} 的值无法转换为 {self.type}: {value!r}") from exc

        if self.minimum is not None and isinstance(coerced, (int, float)):
            coerced = max(self.minimum, coerced)
        if self.maximum is not None and isinstance(coerced, (int, float)):
            coerced = min(self.maximum, coerced)
        if self.choices and str(coerced) not in self.choices:
            raise ValidationError(
                f"设置 {self.key} 只能取 {', '.join(self.choices)}，收到 {coerced!r}"
            )
        return coerced


SETTING_SPECS: tuple[SettingSpec, ...] = (
    # --- 采集调度 ---
    SettingSpec("auto_crawl_enabled", "bool", False, "是否启用自动定时采集"),
    SettingSpec("auto_crawl_interval_minutes", "int", 180, "自动采集间隔（分钟）", minimum=10),
    SettingSpec(
        "skip_crawl_when_credential_degraded", "bool", True,
        "登录凭据处于降级状态时是否跳过自动采集（fail-closed）",
    ),
    # --- 风控 ---
    SettingSpec("credential_failure_threshold", "int", 3, "连续失败多少次进入冷却", minimum=1),
    SettingSpec("credential_cooldown_minutes", "int", 60, "冷却时长（分钟）", minimum=1),
    SettingSpec(
        "credential_relogin_threshold", "int", 3,
        "冷却后再失败多少次判定为需要重新登录", minimum=1,
    ),
    # --- 采集节奏（拟人化） ---
    SettingSpec("xhs_detail_delay_seconds", "float", 5.0, "小红书笔记详情间隔（秒）", minimum=3.0),
    SettingSpec("xhs_scroll_delay_seconds", "float", 3.0, "小红书主页滚动间隔（秒）", minimum=2.0),
    SettingSpec("qq_page_delay_seconds", "float", 0.8, "QQ 说说翻页间隔（秒）", minimum=0.3),
    SettingSpec("qq_max_pages", "int", 150, "QQ 单次采集最大翻页数", minimum=1),
    SettingSpec("crawl_jitter_ratio", "float", 0.35, "延迟随机抖动比例", minimum=0.0, maximum=1.0),
    SettingSpec("crawl_max_concurrent_jobs", "int", 1, "同时执行的采集任务数", minimum=1, maximum=8),
    # --- 媒体 ---
    SettingSpec("media_download_enabled", "bool", True, "是否下载媒体到本地"),
    SettingSpec("media_max_image_mb", "int", 15, "单张图片大小上限（MB）", minimum=1),
    SettingSpec("media_max_video_mb", "int", 300, "单个视频大小上限（MB）", minimum=1),
    # --- 知识库 ---
    SettingSpec("kb_auto_index_enabled", "bool", True, "采集后自动增量建索引"),
    SettingSpec("kb_chunk_size", "int", 480, "分块目标长度（字符）", minimum=120, maximum=2000),
    SettingSpec("kb_chunk_overlap", "int", 80, "分块重叠长度（字符）", minimum=0, maximum=500),
    SettingSpec("kb_vision_enabled", "bool", False, "是否用视觉模型给图片生成描述并入库"),
    SettingSpec("kb_retrieval_top_k", "int", 12, "检索返回的分块数", minimum=1, maximum=100),
    SettingSpec("kb_rrf_k", "int", 60, "RRF 融合常数", minimum=1),
    # --- Agent ---
    SettingSpec("agent_max_tool_iterations", "int", 8, "单轮对话最多工具调用轮数", minimum=1, maximum=30),
    SettingSpec("agent_tools_enabled", "bool", True, "是否允许 Agent 调用工具"),
    SettingSpec("allow_send_to_monitored", "bool", False, "是否允许把通知发给被监控账号"),
    # --- 通知 ---
    SettingSpec("admin_qq", "str", "", "接收通知的管理员 QQ"),
    SettingSpec("admin_name", "str", "", "管理员称呼"),
    SettingSpec("login_notify_enabled", "bool", True, "登录态异常时通知管理员"),
    SettingSpec("server_base_url", "str", "", "对外可访问的服务地址，用于拼接媒体绝对地址"),
    SettingSpec(
        "ai_context_mode", "str", "full", "AI 上下文策略",
        choices=("full", "auto", "smart_summary", "smart_search"),
    ),
    SettingSpec("auto_summary_enabled", "bool", True, "是否自动生成周期总结"),
)

SPEC_BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in SETTING_SPECS}

_CACHE_TTL_SECONDS = 10.0


class SettingsStore:
    """带短 TTL 缓存的设置读写。

    缓存很短是刻意的：设置页改完应当很快生效，同时避免采集循环里每次都打数据库。
    """

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}
        self._loaded_at: float = 0.0

    def _expired(self) -> bool:
        return (time.monotonic() - self._loaded_at) > _CACHE_TTL_SECONDS

    def invalidate(self) -> None:
        self._loaded_at = 0.0

    async def load_all(self, session: AsyncSession, *, force: bool = False) -> dict[str, Any]:
        if not force and self._cache and not self._expired():
            return dict(self._cache)

        rows = (await session.execute(select(AppSetting))).scalars().all()
        stored = {row.key: row.value for row in rows}
        resolved = {
            spec.key: spec.coerce(stored.get(spec.key, spec.default)) for spec in SETTING_SPECS
        }
        self._cache = resolved
        self._loaded_at = time.monotonic()
        return dict(resolved)

    async def get(self, session: AsyncSession, key: str) -> Any:
        spec = SPEC_BY_KEY.get(key)
        if spec is None:
            raise ValidationError(f"未知设置项: {key}")
        return (await self.load_all(session)).get(key, spec.default)

    async def set_many(self, session: AsyncSession, values: dict[str, Any]) -> dict[str, Any]:
        from datetime import datetime

        unknown = sorted(set(values) - set(SPEC_BY_KEY))
        if unknown:
            raise ValidationError(f"未知设置项: {', '.join(unknown)}")

        now = datetime.now()
        for key, raw in values.items():
            coerced = SPEC_BY_KEY[key].coerce(raw)
            statement = (
                pg_insert(AppSetting)
                .values(key=key, value=coerced, updated_at=now)
                .on_conflict_do_update(
                    index_elements=[AppSetting.key], set_={"value": coerced, "updated_at": now}
                )
            )
            await session.execute(statement)

        await session.flush()
        self.invalidate()
        return await self.load_all(session, force=True)

    @staticmethod
    def describe() -> list[dict[str, Any]]:
        return [
            {
                "key": spec.key,
                "type": spec.type,
                "default": spec.default,
                "description": spec.description,
                "minimum": spec.minimum,
                "maximum": spec.maximum,
                "choices": list(spec.choices) if spec.choices else None,
            }
            for spec in SETTING_SPECS
        ]


settings_store = SettingsStore()

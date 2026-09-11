"""基础设施配置：来自环境变量 / .env，进程启动时确定，运行期不变。

用户可调的行为参数（采集节奏、AI 模式等）不放这里，放数据库 app_settings 表，
见 app.core.settings_store。
"""
from __future__ import annotations

from functools import cached_property
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

from app.core.paths import (
    BROWSER_PROFILE_ROOT,
    DEFAULT_LOG_ROOT,
    DEFAULT_STATIC_ROOT,
    PROJECT_ROOT,
    resolve_under_project,
)

ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    # --- 数据库 ---
    database_host: str = "127.0.0.1"
    database_port: int = 5432
    database_name: str = "AI_records_and_reminders"
    database_user: str = "AI_records_and_reminders"
    database_password: str = "change-me"
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # --- 服务 ---
    backend_host: str = "0.0.0.0"
    backend_port: int = 18100
    secret_key: str = "change-me-to-a-random-string"
    admin_api_token: str = ""

    # --- Redis（队列、限流、进度广播）---
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_enabled: bool = True

    # --- NapCat ---
    napcat_ws_url: str = "ws://127.0.0.1:3001"
    napcat_token: str = ""

    # --- 路径 ---
    static_dir: str = ""
    log_dir: str = ""
    browser_profile_dir: str = ""

    # --- 浏览器 ---
    browser_channel: str = "chrome"
    browser_headless: bool = True
    browser_max_concurrency: int = 2
    browser_launch_timeout_ms: int = 60_000

    # --- 知识库 ---
    embedding_dimensions: int = 1536

    log_level: str = "INFO"

    model_config = {
        "env_file": ENV_FILE,
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @field_validator("browser_channel")
    @classmethod
    def _normalize_channel(cls, value: str) -> str:
        return (value or "").strip().lower()

    @cached_property
    def static_path(self) -> Path:
        return resolve_under_project(self.static_dir, default=DEFAULT_STATIC_ROOT)

    @cached_property
    def log_path(self) -> Path:
        return resolve_under_project(self.log_dir, default=DEFAULT_LOG_ROOT)

    @cached_property
    def browser_profile_path(self) -> Path:
        return resolve_under_project(self.browser_profile_dir, default=BROWSER_PROFILE_ROOT)

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.database_user}:{self.database_password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )

    @property
    def database_url_sync(self) -> str:
        return (
            f"postgresql://{self.database_user}:{self.database_password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )

    @property
    def has_strong_admin_token(self) -> bool:
        if self.admin_api_token.strip():
            return True
        return self.secret_key.strip() not in {"", "change-me-to-a-random-string"}

    @property
    def effective_admin_token(self) -> str:
        if self.admin_api_token.strip():
            return self.admin_api_token.strip()
        if self.has_strong_admin_token:
            return self.secret_key.strip()
        return ""

    def ensure_directories(self) -> None:
        from app.core.paths import MEDIA_SUBDIRS

        self.static_path.mkdir(parents=True, exist_ok=True)
        self.log_path.mkdir(parents=True, exist_ok=True)
        self.browser_profile_path.mkdir(parents=True, exist_ok=True)
        for subdir in MEDIA_SUBDIRS:
            (self.static_path / subdir).mkdir(parents=True, exist_ok=True)


settings = Settings()

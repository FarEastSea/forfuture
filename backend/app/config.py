from pydantic_settings import BaseSettings
from pathlib import Path
from typing import Optional
from app.paths import PROJECT_ROOT, STATIC_ROOT


ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    # 数据库
    database_host: str = "127.0.0.1"
    database_port: int = 5432
    database_name: str = "AI_records_and_reminders"
    database_user: str = "AI_records_and_reminders"
    database_password: str = "change-me"

    # 后端
    backend_host: str = "0.0.0.0"
    backend_port: int = 18100
    secret_key: str = "change-me-to-a-random-string"

    # NapCat
    napcat_ws_url: str = "ws://127.0.0.1:3001"
    napcat_token: str = ""

    # 静态文件
    static_dir: str = str(STATIC_ROOT)

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

    model_config = {
        "env_file": ENV_FILE,
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()

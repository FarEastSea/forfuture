"""日志：控制台 + JSONL 文件，供设置页的日志查看器读取。"""
from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime
from typing import Any

from app.core.config import settings

_CONFIGURED = False

_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "thread", "threadName", "taskName",
}


class JsonLinesFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")}
        if extras:
            payload["extra"] = {k: _safe(v) for k, v in extras.items()}
        return json.dumps(payload, ensure_ascii=False)


def _safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return repr(value)


def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    settings.log_path.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)-5.5s [%(name)s] %(message)s"))
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        settings.log_path / "app.jsonl",
        maxBytes=20 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonLinesFormatter())
    root.addHandler(file_handler)

    for noisy in ("httpx", "httpcore", "asyncio", "urllib3", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

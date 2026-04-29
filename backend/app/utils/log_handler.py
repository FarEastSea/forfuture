"""
统一日志系统 — 基于文件存储

关键设计：
- 日志写入 JSON Lines 文件（每行一个JSON对象）
- API 直接读文件，不依赖任何内存状态
- 完全不受 uvicorn reload、多进程、模块重载影响
- 文件写入用 append 模式 + threading.Lock（进程内安全）
- 定期截断防止文件无限增长
"""
import logging
import json
import os
import threading
from datetime import datetime
from typing import Optional
from app.paths import LOG_ROOT

# 日志文件路径：backend/logs/app.jsonl
_LOG_DIR = os.fspath(LOG_ROOT)
_LOG_FILE = os.path.join(_LOG_DIR, "app.jsonl")
_MAX_LINES = 2000
_write_lock = threading.Lock()
_write_count = 0  # 写入计数，用于定期截断检查

_HANDLER_MARKER = "__ai_records_file_log__"


class FileLogHandler(logging.Handler):
    """将日志以 JSON Lines 格式写入文件"""

    def __init__(self):
        super().__init__()
        self._marker = _HANDLER_MARKER
        os.makedirs(_LOG_DIR, exist_ok=True)

    def emit(self, record: logging.LogRecord):
        global _write_count
        try:
            entry = {
                "time": datetime.fromtimestamp(record.created).strftime("%Y-%m-%dT%H:%M:%S"),
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": self.format(record),
                "module": record.module,
            }
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            with _write_lock:
                with open(_LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(line)
                _write_count += 1
                # 每500条检查一次是否需要截断
                if _write_count >= 500:
                    _write_count = 0
                    _truncate_if_needed()
        except Exception:
            pass


def _truncate_if_needed():
    """如果文件行数超过2倍限制，截断到最新的 _MAX_LINES 行"""
    try:
        if not os.path.exists(_LOG_FILE):
            return
        with open(_LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > _MAX_LINES * 2:
            with open(_LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-_MAX_LINES:])
    except Exception:
        pass


# ===== 供 API 调用的函数 =====

def get_logs(
    level: Optional[str] = None,
    module: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    """从日志文件读取并过滤日志（最新在前）"""
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }
    min_level = level_map.get(level, logging.DEBUG) if level else logging.DEBUG

    try:
        if not os.path.exists(_LOG_FILE):
            return []
        with open(_LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []

    result = []
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            log = json.loads(raw)
        except Exception:
            continue

        log_level = level_map.get(log.get("level", ""), 0)
        if log_level < min_level:
            continue
        if module:
            m = module.lower()
            if m not in log.get("logger", "").lower() and m not in log.get("module", "").lower():
                continue
        if keyword and keyword.lower() not in log.get("message", "").lower():
            continue
        result.append(log)
        if len(result) >= limit:
            break
    return result


def get_log_count() -> int:
    """获取日志总行数"""
    try:
        if not os.path.exists(_LOG_FILE):
            return 0
        with open(_LOG_FILE, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def clear_logs():
    """清空日志文件"""
    try:
        with _write_lock:
            with open(_LOG_FILE, "w", encoding="utf-8") as f:
                pass
    except Exception:
        pass


def setup_logging():
    """初始化日志系统 — 在 main.py lifespan 中调用"""
    root = logging.getLogger()

    # 移除所有旧的自定义Handler（用 marker 属性匹配 + 类名兜底）
    for h in root.handlers[:]:
        if getattr(h, '_marker', None) in (_HANDLER_MARKER, "__ai_records_unified_log__"):
            root.removeHandler(h)
        elif type(h).__name__ in ('InMemoryLogHandler', 'UnifiedLogHandler', 'FileLogHandler'):
            root.removeHandler(h)

    # 创建文件日志Handler
    handler = FileLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.setLevel(logging.DEBUG)
    root.addHandler(handler)

    root.setLevel(logging.DEBUG)

    for name in ("uvicorn", "uvicorn.error", "fastapi", "app"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    for name in ("apscheduler", "apscheduler.scheduler", "apscheduler.executors"):
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # 抑制WebSocket协议层的DEBUG日志（ping/pong每秒一次，极度频繁）
    for name in ("websockets", "websockets.protocol", "websockets.server",
                 "websockets.client", "uvicorn.protocols", "httpcore",
                 "httpx", "hpack"):
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger("app").info("日志系统已初始化（文件存储模式）")

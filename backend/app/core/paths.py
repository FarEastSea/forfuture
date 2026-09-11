"""项目路径解析。

所有路径都锚定在项目根目录上，绝不依赖进程工作目录——历史上 STATIC_DIR=./static 配合
release 目录下启动的 gunicorn，把抓取到的媒体写进了会被部署清理掉的 release 目录。
"""
from __future__ import annotations

from pathlib import Path


def _discover_project_root(anchor: Path) -> Path:
    """从当前文件向上找到同时包含 backend 与 (frontend 或 .env) 的目录。"""
    current = anchor.absolute()
    base = current if current.is_dir() else current.parent
    candidates = [base, *base.parents]

    for candidate in candidates:
        if (candidate / "backend").is_dir() and (
            (candidate / "frontend").is_dir() or (candidate / ".env").is_file()
        ):
            return candidate

    for candidate in candidates:
        if candidate.name == "backend":
            return candidate.parent

    return candidates[min(2, len(candidates) - 1)]


PROJECT_ROOT = _discover_project_root(Path(__file__))
BACKEND_ROOT = PROJECT_ROOT / "backend"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
DEFAULT_STATIC_ROOT = BACKEND_ROOT / "static"
DEFAULT_LOG_ROOT = BACKEND_ROOT / "logs"
# 沿用历史上的 browser_data 目录，避免丢掉已抢救的小红书登录会话。
BROWSER_PROFILE_ROOT = BACKEND_ROOT / "browser_data"

# 媒体子目录名沿用历史命名，保证已下载资源的 /static/... 路径继续有效。
MEDIA_SUBDIRS = ("qq_images", "xhs_images", "avatars", "qq_videos", "xhs_videos")


def resolve_under_project(value: str | Path, *, default: Path) -> Path:
    """把配置里的路径解析成绝对路径。

    相对路径一律相对 PROJECT_ROOT 解析，而不是相对进程 cwd，避免服务从不同目录启动时
    把持久化数据写到别的地方。
    """
    if not value:
        return default
    path = Path(str(value).strip())
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()

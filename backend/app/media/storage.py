"""媒体落盘。新下载用内容哈希命名；历史 /static/... 路径原样保留，文件绝不改名。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from app.core.config import settings
from app.core.paths import MEDIA_SUBDIRS
from app.domain.enums import MediaKind, Platform

KIND_TO_SUBDIR = {
    (Platform.QQ.value, MediaKind.IMAGE.value): "qq_images",
    (Platform.XHS.value, MediaKind.IMAGE.value): "xhs_images",
    (Platform.QQ.value, MediaKind.VIDEO.value): "qq_videos",
    (Platform.XHS.value, MediaKind.VIDEO.value): "xhs_videos",
    (Platform.QQ.value, MediaKind.AVATAR.value): "avatars",
    (Platform.XHS.value, MediaKind.AVATAR.value): "avatars",
}


def public_path(subdir: str, filename: str) -> str:
    return f"/static/{subdir}/{filename}"


def disk_path(local_path: str) -> Path:
    relative = local_path[len("/static/") :] if local_path.startswith("/static/") else local_path
    return settings.static_path / relative


def hashed_filename(content: bytes, hinted_ext: str | None) -> str:
    digest = hashlib.sha256(content).hexdigest()
    ext = (hinted_ext or "").lstrip(".")
    if not ext:
        ext = "bin"
    return f"{digest}.{ext}"


def subdir_for(platform: str, kind: str) -> str:
    mapped = KIND_TO_SUBDIR.get((platform, kind))
    if mapped:
        return mapped
    if kind == MediaKind.AVATAR.value:
        return "avatars"
    return MEDIA_SUBDIRS[0]


def guess_ext(url: str, content_type: str | None) -> str:
    mapping = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
        "video/mp4": "mp4",
        "video/webm": "webm",
    }
    if content_type:
        mime = content_type.split(";")[0].strip().lower()
        if mime in mapping:
            return mapping[mime]
    path = url.split("?", 1)[0].rsplit(".", 1)
    if len(path) == 2 and 1 <= len(path[1]) <= 5:
        return path[1].lower()
    if "!nd_dft" in url and "webp" in url.lower():
        return "webp"
    return "bin"

"""每凭据一份内部一致的指纹档案。

不再往页面里注入 20 段互相矛盾的 JS。UA、平台、硬件、屏幕、时区必须联动，
并且持久化——同一个账号每次启动都用同一份指纹。
"""
from __future__ import annotations

import hashlib
from typing import Any

# 固定档位，避免随机组合出 Linux+Win32+NVIDIA 这种自相矛盾的画像。
_PROFILES = (
    {
        "name": "win-chrome-136",
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
        "platform": "Win32",
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "viewport": {"width": 1280, "height": 800},
        "screen": {"width": 1920, "height": 1080},
        "hardware_concurrency": 8,
        "device_memory": 8,
        "color_scheme": "light",
    },
    {
        "name": "win-chrome-136-laptop",
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
        "platform": "Win32",
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "viewport": {"width": 1366, "height": 768},
        "screen": {"width": 1366, "height": 768},
        "hardware_concurrency": 4,
        "device_memory": 8,
        "color_scheme": "light",
    },
)


def fingerprint_for_key(stable_key: str) -> dict[str, Any]:
    digest = hashlib.sha256(stable_key.encode("utf-8")).digest()
    return dict(_PROFILES[digest[0] % len(_PROFILES)])


def validate_fingerprint(fp: dict[str, Any]) -> list[str]:
    """返回不一致项。Linux 服务器上声称 Win32 是可以的（我们就是要模拟桌面 Chrome），
    但不能同时声称 Android 又给桌面 UA。
    """
    problems: list[str] = []
    ua = fp.get("user_agent") or ""
    platform = fp.get("platform") or ""
    if "Windows" in ua and platform not in {"Win32", "Win64"}:
        problems.append("UA 是 Windows 但 platform 不是 Win32")
    if "Linux" in ua and "Windows" in platform:
        problems.append("UA 是 Linux 但 platform 是 Windows")
    viewport = fp.get("viewport") or {}
    screen = fp.get("screen") or {}
    if viewport.get("width") and screen.get("width") and viewport["width"] > screen["width"]:
        problems.append("viewport 宽于 screen")
    return problems

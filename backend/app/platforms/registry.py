"""平台注册表。新增平台只需要在这里登记，采集引擎不用改。"""
from __future__ import annotations

from app.core.errors import PlatformNotSupportedError
from app.domain.enums import Platform
from app.platforms.base import PlatformPlugin

_PLUGINS: dict[str, PlatformPlugin] = {}


def register(plugin: PlatformPlugin) -> None:
    _PLUGINS[plugin.name.value] = plugin


def get_platform(name: str | Platform) -> PlatformPlugin:
    key = name.value if isinstance(name, Platform) else str(name)
    plugin = _PLUGINS.get(key)
    if plugin is None:
        raise PlatformNotSupportedError(f"未注册的平台: {key}")
    return plugin


def load_builtin_platforms() -> None:
    if _PLUGINS:
        return
    from app.platforms.qq import build_plugin as build_qq
    from app.platforms.xhs import build_plugin as build_xhs

    register(build_qq())
    register(build_xhs())

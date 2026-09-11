"""patchright 内核封装。优先系统 Chrome，找不到再降级到 bundled Chromium。"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("app.browser")


def _load_storage_state(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    path = Path(value)
    return json.loads(path.read_text(encoding="utf-8"))


async def _restore_storage_state(context: Any, value: str | dict[str, Any]) -> None:
    """为 persistent context 恢复普通 context 的 storage_state 语义。"""
    state = _load_storage_state(value)
    cookies = state.get("cookies") or []
    if cookies:
        await context.add_cookies(cookies)

    origins: dict[str, dict[str, str]] = {}
    for origin in state.get("origins") or []:
        origin_url = str(origin.get("origin") or "")
        if not origin_url:
            continue
        origins[origin_url] = {
            str(item["name"]): str(item.get("value") or "")
            for item in origin.get("localStorage") or []
            if item.get("name")
        }
    if origins:
        payload = json.dumps(origins)
        await context.add_init_script(
            """
(() => {
  const states = %s;
  const current = states[window.location.origin];
  if (!current) return;
  for (const [key, value] of Object.entries(current)) {
    window.localStorage.setItem(key, value);
  }
})();
""" % payload
        )


def _import_async_playwright():
    try:
        from patchright.async_api import async_playwright

        return async_playwright, "patchright"
    except ImportError:
        from playwright.async_api import async_playwright

        return async_playwright, "playwright"


class BrowserKernel:
    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self.backend: str = "unknown"
        self.using_system_chrome: bool = False

    async def start(self) -> None:
        factory, backend = _import_async_playwright()
        self.backend = backend
        self._playwright = await factory().start()
        launch_args = ["--disable-blink-features=AutomationControlled"]
        channel = settings.browser_channel or None
        try:
            self._browser = await self._playwright.chromium.launch(
                headless=settings.browser_headless,
                channel=channel if channel else None,
                args=launch_args,
                timeout=settings.browser_launch_timeout_ms,
            )
            self.using_system_chrome = bool(channel)
            logger.info("浏览器内核 %s 已启动 channel=%s", backend, channel or "bundled")
        except Exception as exc:
            if not channel:
                raise
            logger.warning("系统 Chrome 不可用，降级到 bundled Chromium: %s", exc)
            self._browser = await self._playwright.chromium.launch(
                headless=settings.browser_headless,
                args=launch_args,
                timeout=settings.browser_launch_timeout_ms,
            )
            self.using_system_chrome = False

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    @asynccontextmanager
    async def persistent_context(
        self,
        user_data_dir: str,
        *,
        fingerprint: dict[str, Any] | None = None,
        proxy: str | None = None,
        storage_state: str | dict | None = None,
    ) -> AsyncIterator[Any]:
        if self._playwright is None:
            await self.start()
        options: dict[str, Any] = {
            "headless": settings.browser_headless,
            "args": ["--disable-blink-features=AutomationControlled"],
            "viewport": (fingerprint or {}).get("viewport") or {"width": 1280, "height": 800},
            "locale": (fingerprint or {}).get("locale") or "zh-CN",
            "timezone_id": (fingerprint or {}).get("timezone_id") or "Asia/Shanghai",
            "user_agent": (fingerprint or {}).get("user_agent"),
            "color_scheme": (fingerprint or {}).get("color_scheme") or "light",
        }
        channel = settings.browser_channel if self.using_system_chrome else None
        if channel:
            options["channel"] = channel
        if proxy:
            options["proxy"] = {"server": proxy}
        context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir, **{k: v for k, v in options.items() if v is not None}
        )
        try:
            if storage_state:
                await _restore_storage_state(context, storage_state)
            yield context
        finally:
            await context.close()


kernel = BrowserKernel()

"""浏览器并发闸门。一台机器同时开太多 Chromium 会把内存打满。"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from app.browser.fingerprint import fingerprint_for_key, validate_fingerprint
from app.browser.kernel import kernel
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("app.browser.pool")


class BrowserPool:
    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(settings.browser_max_concurrency)

    @asynccontextmanager
    async def session(
        self,
        *,
        credential_key: str,
        profile_dir: str | Path,
        fingerprint: dict[str, Any] | None = None,
        proxy: str | None = None,
        storage_state: dict | None = None,
    ) -> AsyncIterator[Any]:
        async with self._semaphore:
            fp = fingerprint or fingerprint_for_key(credential_key)
            problems = validate_fingerprint(fp)
            if problems:
                logger.warning("指纹一致性告警 %s: %s", credential_key, problems)
            Path(profile_dir).mkdir(parents=True, exist_ok=True)
            async with kernel.persistent_context(
                str(profile_dir),
                fingerprint=fp,
                proxy=proxy,
                storage_state=storage_state,
            ) as context:
                yield context


browser_pool = BrowserPool()

"""拟人化交互：贝塞尔鼠标、变速滚动、随机停顿。"""
from __future__ import annotations

import asyncio
import math
import random
from typing import Any


async def pause(base_seconds: float, jitter_ratio: float = 0.35) -> None:
    spread = base_seconds * jitter_ratio
    await asyncio.sleep(max(0.05, random.uniform(base_seconds - spread, base_seconds + spread)))


def _bezier(p0: float, p1: float, p2: float, t: float) -> float:
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * p1 + t**2 * p2


async def move_mouse(page: Any, x: float, y: float, *, steps: int | None = None) -> None:
    start = page.mouse._position if hasattr(page.mouse, "_position") else (0.0, 0.0)
    sx, sy = (start if isinstance(start, tuple) else (0.0, 0.0))
    control_x = (sx + x) / 2 + random.uniform(-80, 80)
    control_y = (sy + y) / 2 + random.uniform(-60, 60)
    total = steps or random.randint(12, 22)
    for i in range(1, total + 1):
        t = i / total
        eased = (1 - math.cos(math.pi * t)) / 2
        px = _bezier(sx, control_x, x, eased)
        py = _bezier(sy, control_y, y, eased)
        await page.mouse.move(px, py)
        await asyncio.sleep(random.uniform(0.008, 0.025))


async def human_click(page: Any, element: Any) -> None:
    box = await element.bounding_box()
    if not box:
        await element.click()
        return
    x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
    await move_mouse(page, x, y)
    await pause(0.08, 0.4)
    await page.mouse.click(x, y)


async def human_scroll(page: Any, distance: int | None = None) -> None:
    delta = distance or random.randint(420, 780)
    remaining = delta
    while remaining > 0:
        step = min(remaining, random.randint(80, 180))
        await page.mouse.wheel(0, step)
        remaining -= step
        await asyncio.sleep(random.uniform(0.04, 0.12))

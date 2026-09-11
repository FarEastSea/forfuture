"""NapCat connection registry and message transport."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from fastapi import WebSocket

from app.core.events import EventType, event_bus
from app.core.logging import get_logger

logger = get_logger("app.notifications.napcat")


class NapCatClient:
    def __init__(self) -> None:
        self.connections: dict[str, WebSocket] = {}
        self._status: dict[str, dict[str, Any]] = {}
        self._response_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def on_connect(self, qq: str, websocket: WebSocket) -> None:
        self.connections[qq] = websocket
        self._status[qq] = {"connected": True, "qq": qq, "at": datetime.now().isoformat()}
        await event_bus.publish(EventType.NAPCAT_STATUS, self.get_status())

    async def on_disconnect(self, qq: str) -> None:
        self.connections.pop(qq, None)
        self._status.pop(qq, None)
        await event_bus.publish(EventType.NAPCAT_STATUS, self.get_status())

    async def on_message(self, qq: str, message: dict[str, Any]) -> None:
        message_type = str(message.get("type") or "")
        waiter = self._response_waiters.pop(message_type, None)
        if waiter is not None and not waiter.done():
            waiter.set_result(message.get("payload") or {})
        if message_type == "status":
            self._status[qq] = {**self._status.get(qq, {}), **(message.get("payload") or {})}
            await event_bus.publish(EventType.NAPCAT_STATUS, self.get_status())

    def get_status(self) -> dict[str, Any]:
        return {
            "connected_count": len(self.connections),
            "connections": list(self._status.values()),
        }

    async def send_message(self, target_qq: str, messages: list[dict[str, Any]]) -> bool:
        if not self.connections:
            return False
        websocket = next(iter(self.connections.values()))
        await websocket.send_text(
            json.dumps(
                {
                    "type": "send_message",
                    "payload": {"target_qq": target_qq, "messages": messages},
                },
                ensure_ascii=False,
            )
        )
        return True

    async def request(
        self,
        request_type: str,
        response_type: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 15,
    ) -> dict[str, Any]:
        if not self.connections:
            raise RuntimeError("没有可用的 NapCat 连接")
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._response_waiters[response_type] = waiter
        websocket = next(iter(self.connections.values()))
        try:
            message: dict[str, Any] = {"type": request_type}
            if payload:
                message["payload"] = payload
            await websocket.send_text(json.dumps(message, ensure_ascii=False))
            return await asyncio.wait_for(waiter, timeout=timeout)
        finally:
            self._response_waiters.pop(response_type, None)


napcat_client = NapCatClient()

"""实时事件 WebSocket。"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket

from app.core.events import event_bus
from app.core.security import authorize_websocket

router = APIRouter()


@router.websocket("/ws/v2/events")
async def events_socket(websocket: WebSocket) -> None:
    if not await authorize_websocket(websocket):
        return
    async with event_bus.subscribe() as queue:
        while True:
            event = await queue.get()
            await websocket.send_json(
                {"type": event.type, "payload": event.payload, "at": event.at}
            )

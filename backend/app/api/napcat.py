"""NapCat plugin WebSocket. This transport remains stable across API versions."""
from __future__ import annotations

import asyncio
import json
import secrets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.core.config import settings
from app.core.logging import get_logger
from app.notifications.napcat import napcat_client

router = APIRouter()
logger = get_logger("app.api.napcat")


def _authorized(websocket: WebSocket) -> bool:
    expected = (settings.napcat_token or settings.effective_admin_token).strip()
    provided = (websocket.headers.get("x-napcat-token") or "").strip()
    return bool(expected and provided and secrets.compare_digest(expected, provided))


@router.websocket("/ws/napcat")
async def napcat_socket(websocket: WebSocket) -> None:
    if not _authorized(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    await websocket.send_json({"type": "welcome"})
    connection_id = "unknown"

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(25)
            await websocket.send_json({"type": "ping"})

    ping_task = asyncio.create_task(heartbeat())
    try:
        auth = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=30))
        connection_id = str(auth.get("qq") or "unknown")
        await napcat_client.on_connect(connection_id, websocket)
        while True:
            message = json.loads(await websocket.receive_text())
            message_type = message.get("type")
            if message_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            if message_type == "pong":
                continue
            if message_type == "auth":
                next_id = str(message.get("qq") or "unknown")
                if next_id != connection_id:
                    await napcat_client.on_disconnect(connection_id)
                    connection_id = next_id
                    await napcat_client.on_connect(connection_id, websocket)
                continue
            await napcat_client.on_message(connection_id, message)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except Exception:
        logger.exception("NapCat WebSocket 异常")
    finally:
        ping_task.cancel()
        await napcat_client.on_disconnect(connection_id)

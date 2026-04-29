from fastapi import WebSocket
import json
import logging
import asyncio
from typing import Optional
from datetime import datetime
from collections import deque

logger = logging.getLogger(__name__)


class NapCatClient:
    def __init__(self):
        self.connections: dict[str, WebSocket] = {}
        self.frontend_clients: list[WebSocket] = []
        self._status: dict[str, dict] = {}
        self._message_callbacks: list = []
        self._logs: deque = deque(maxlen=200)  # 扩大日志缓冲，200条足够回看
        self._response_waiters: dict[str, asyncio.Future] = {}  # type -> Future，用于请求-响应模式

    def _add_log(self, level: str, message: str):
        self._logs.append({
            "time": datetime.now().isoformat(),
            "level": level,
            "message": message,
        })

    def get_logs(self) -> list:
        return list(self._logs)

    def on_connect(self, qq: str, ws: WebSocket):
        self.connections[qq] = ws
        self._status[qq] = {"connected": True, "qq": qq}
        self._add_log("info", f"NapCat插件已连接: QQ={qq}")
        asyncio.create_task(self._notify_frontend({"type": "napcat_connected", "qq": qq}))

    def on_disconnect(self, qq: str):
        self.connections.pop(qq, None)
        self._status.pop(qq, None)
        self._add_log("warn", f"NapCat插件已断开: QQ={qq}")
        asyncio.create_task(self._notify_frontend({"type": "napcat_disconnected", "qq": qq}))

    async def on_message(self, qq: str, msg: dict):
        msg_type = msg.get("type", "")

        # 检查是否有等待此类型响应的请求
        if msg_type in self._response_waiters:
            waiter = self._response_waiters.pop(msg_type)
            if not waiter.done():
                waiter.set_result(msg.get("payload", {}))
            return

        if msg_type == "status":
            self._status[qq] = {**self._status.get(qq, {}), **msg.get("payload", {})}
        elif msg_type == "message_received":
            for cb in self._message_callbacks:
                try:
                    await cb(qq, msg.get("payload", {}))
                except Exception as e:
                    logger.error(f"消息回调错误: {e}")
        await self._notify_frontend({"type": "napcat_event", "qq": qq, "data": msg})

    def get_status(self) -> dict:
        return {
            "connections": list(self._status.values()),
            "connected_count": len(self.connections),
        }

    async def get_login_qrcode(self) -> Optional[str]:
        """请求NapCat生成登录二维码"""
        for qq, ws in self.connections.items():
            try:
                await ws.send_text(json.dumps({"type": "get_qrcode"}))
                return "qrcode_requested"
            except Exception:
                pass
        return None

    async def send_message(self, target_qq: str, messages: list[dict]) -> bool:
        """通过NapCat发送消息"""
        if not self.connections:
            logger.warning("没有可用的NapCat连接")
            return False

        ws = next(iter(self.connections.values()))
        payload = {
            "type": "send_message",
            "payload": {
                "target_qq": target_qq,
                "messages": messages,
            }
        }
        try:
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
            return True
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            return False

    async def send_render_message(self, target_qq: str, html: str, messages: list[dict] = None) -> bool:
        """通过puppeteer渲染HTML后发送图片"""
        if not self.connections:
            return False

        ws = next(iter(self.connections.values()))
        payload = {
            "type": "render_and_send",
            "payload": {
                "target_qq": target_qq,
                "html": html,
                "extra_messages": messages or [],
            }
        }
        try:
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
            return True
        except Exception as e:
            logger.error(f"渲染发送失败: {e}")
            return False

    def add_frontend_client(self, ws: WebSocket):
        self.frontend_clients.append(ws)

    def remove_frontend_client(self, ws: WebSocket):
        if ws in self.frontend_clients:
            self.frontend_clients.remove(ws)

    async def _notify_frontend(self, data: dict):
        dead = []
        for ws in self.frontend_clients:
            try:
                await ws.send_text(json.dumps(data, ensure_ascii=False))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.frontend_clients.remove(ws)

    def on_message_received(self, callback):
        self._message_callbacks.append(callback)

    async def request(self, request_type: str, response_type: str,
                      payload: dict = None, timeout: float = 15) -> dict:
        """向NapCat插件发送请求并等待指定类型的响应。
        
        Args:
            request_type: 发送给插件的消息type
            response_type: 期望插件返回的消息type
            payload: 请求携带的数据
            timeout: 等待超时秒数
        Returns:
            插件响应的payload字典
        Raises:
            RuntimeError: 无可用连接
            asyncio.TimeoutError: 等待超时
        """
        if not self.connections:
            raise RuntimeError("没有可用的NapCat插件连接")

        ws = next(iter(self.connections.values()))
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._response_waiters[response_type] = future

        try:
            msg = {"type": request_type}
            if payload:
                msg["payload"] = payload
            await ws.send_text(json.dumps(msg, ensure_ascii=False))
            return await asyncio.wait_for(future, timeout=timeout)
        except Exception:
            self._response_waiters.pop(response_type, None)
            raise


napcat_client = NapCatClient()

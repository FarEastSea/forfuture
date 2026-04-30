from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import json
import logging
import asyncio
import base64

from app.security import get_admin_ws_accept_subprotocol, require_admin_websocket, require_napcat_websocket

logger = logging.getLogger(__name__)
router = APIRouter()

# NapCat插件WebSocket连接管理
napcat_connections: dict[str, WebSocket] = {}

# 跟踪哪些平台有活跃的浏览器预览 WebSocket 连接
active_preview_platforms: set[str] = set()


@router.websocket("/ws/napcat")
async def napcat_ws(websocket: WebSocket):
    await require_napcat_websocket(websocket)
    await websocket.accept()
    conn_id = None
    client_info = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
    logger.info(f"NapCat WebSocket连接请求: {client_info}")
    ping_task = None
    # 发送锁 — Starlette WebSocket 不支持并发 send，必须用锁保护
    send_lock = asyncio.Lock()

    async def safe_send(data: str):
        async with send_lock:
            await websocket.send_text(data)

    try:
        # 立即发送welcome消息（连接后立即有数据交互，防止代理因空闲断连）
        await safe_send(json.dumps({"type": "welcome"}))

        # 立即启动服务端心跳
        async def server_ping():
            try:
                while True:
                    await asyncio.sleep(25)
                    await safe_send(json.dumps({"type": "ping"}))
            except Exception:
                pass
        ping_task = asyncio.create_task(server_ping())

        # 等待插件发送身份认证（30秒超时保护）
        try:
            auth_data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
        except asyncio.TimeoutError:
            logger.warning(f"NapCat插件认证超时(30s)，断开: {client_info}")
            return

        auth = json.loads(auth_data)
        conn_id = auth.get("qq", "unknown")
        napcat_connections[conn_id] = websocket
        logger.info(f"NapCat插件已认证: QQ={conn_id}, 来源={client_info}")

        # 通知napcat_client
        from app.services.napcat_client import napcat_client
        napcat_client.on_connect(conn_id, websocket)

        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            msg_type = msg.get("type", "")

            # 心跳响应
            if msg_type in ("ping", "pong"):
                if msg_type == "ping":
                    await safe_send(json.dumps({"type": "pong"}))
                continue

            # 重新认证
            if msg_type == "auth":
                new_qq = msg.get("qq", "unknown")
                if new_qq != "unknown" and new_qq != conn_id:
                    logger.info(f"NapCat插件更新QQ号: {conn_id} → {new_qq}")
                    if conn_id in napcat_connections:
                        del napcat_connections[conn_id]
                    napcat_client.on_disconnect(conn_id)
                    conn_id = new_qq
                    napcat_connections[conn_id] = websocket
                    napcat_client.on_connect(conn_id, websocket)
                continue

            await napcat_client.on_message(conn_id, msg)

    except WebSocketDisconnect as wd:
        logger.info(f"NapCat插件断开连接: QQ={conn_id}, code={wd.code}, reason={wd.reason}")
    except Exception as e:
        logger.error(f"NapCat WebSocket错误: {type(e).__name__}: {e}")
    finally:
        if ping_task:
            ping_task.cancel()
        if conn_id and conn_id in napcat_connections:
            del napcat_connections[conn_id]
            from app.services.napcat_client import napcat_client
            napcat_client.on_disconnect(conn_id)


@router.websocket("/ws/client")
async def client_ws(websocket: WebSocket):
    """前端WebSocket，用于实时推送状态更新"""
    await require_admin_websocket(websocket)
    await websocket.accept(subprotocol=get_admin_ws_accept_subprotocol(websocket))
    from app.services.napcat_client import napcat_client
    napcat_client.add_frontend_client(websocket)
    send_lock = asyncio.Lock()

    async def safe_send(data: str):
        async with send_lock:
            await websocket.send_text(data)

    # 发送当前 NapCat 连接状态
    try:
        status = napcat_client.get_status()
        if status["connected_count"] > 0:
            for conn in status["connections"]:
                await safe_send(json.dumps({
                    "type": "napcat_connected",
                    "qq": conn.get("qq", "unknown")
                }))
        else:
            await safe_send(json.dumps({"type": "napcat_disconnected"}))
    except Exception as e:
        logger.error(f"发送初始NapCat状态失败: {e}")

    # 启动服务端心跳
    ping_task = None
    async def server_ping():
        try:
            while True:
                await asyncio.sleep(25)
                await safe_send(json.dumps({"type": "server_ping"}))
        except Exception:
            pass
    ping_task = asyncio.create_task(server_ping())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                if msg.get("type") == "ping":
                    await safe_send(json.dumps({"type": "pong"}))
            except (json.JSONDecodeError, TypeError):
                pass
    except WebSocketDisconnect:
        pass
    finally:
        if ping_task:
            ping_task.cancel()
        napcat_client.remove_frontend_client(websocket)


@router.websocket("/ws/browser-preview")
async def browser_preview_ws(websocket: WebSocket):
    """实时推送 Playwright 浏览器截图到前端，实现"远程桌面"效果。
    
    前端连接后发送 JSON: {"platform": "qq"} 或 {"platform": "xhs"}
    服务端以 ~30 FPS 持续推送:
      {"type": "frame", "screenshot": "<base64 jpeg>", "url": "...", "title": "...", "status": "...", "detail": "..."}
      {"type": "status", "status": "...", "detail": "..."}
      {"type": "closed", "reason": "..."}
    """
    await require_admin_websocket(websocket)
    await websocket.accept(subprotocol=get_admin_ws_accept_subprotocol(websocket))
    platform = None
    stream_task = None

    try:
        # 等待前端指定平台
        try:
            init_data = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            msg = json.loads(init_data)
            platform = msg.get("platform", "").lower()
        except (asyncio.TimeoutError, json.JSONDecodeError):
            await websocket.send_text(json.dumps({"type": "error", "message": "请发送 {platform: 'qq'|'xhs'}"}))
            return

        if platform not in ("qq", "xhs"):
            await websocket.send_text(json.dumps({"type": "error", "message": f"不支持的平台: {platform}"}))
            return

        logger.info(f"浏览器实时预览 WebSocket 已连接: platform={platform}")
        active_preview_platforms.add(platform)

        async def stream_screenshots():
            """持续截图并推送"""
            no_browser_count = 0
            while True:
                try:
                    crawler = _get_crawler(platform)
                    frame_data = await crawler.get_live_frame()
                    if frame_data:
                        no_browser_count = 0
                        await websocket.send_text(json.dumps({
                            "type": "frame",
                            **frame_data,
                        }))
                    else:
                        no_browser_count += 1
                        # 浏览器未启动时发送状态
                        await websocket.send_text(json.dumps({
                            "type": "status",
                            "status": crawler.login_status,
                            "detail": crawler.login_status_detail,
                            "browser_active": False,
                        }))
                        if no_browser_count > 120:  # ~2分钟无浏览器，关闭
                            await websocket.send_text(json.dumps({
                                "type": "closed",
                                "reason": "浏览器已关闭",
                            }))
                            break
                except WebSocketDisconnect:
                    break
                except Exception as e:
                    logger.debug(f"浏览器预览截图异常: {type(e).__name__}: {e}")
                    try:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": str(e),
                        }))
                    except Exception:
                        break
                await asyncio.sleep(0.033)  # ~30 FPS

        stream_task = asyncio.create_task(stream_screenshots())

        # 同时监听前端消息（点击、关闭等）
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                msg_type = msg.get("type", "")
                if msg_type == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                elif msg_type == "click":
                    # 支持前端点击转发到浏览器
                    x, y = msg.get("x", 0), msg.get("y", 0)
                    await _forward_click(platform, x, y)
                elif msg_type == "keypress":
                    # 支持前端键盘输入转发到浏览器
                    key = msg.get("key", "")
                    text = msg.get("text", "")
                    await _forward_key(platform, key, text)
                elif msg_type == "type_text":
                    # 批量输入文字（如验证码）
                    text = msg.get("text", "")
                    await _forward_type_text(platform, text)
                elif msg_type == "scroll":
                    # 支持滚动
                    x, y = msg.get("x", 0), msg.get("y", 0)
                    delta_x, delta_y = msg.get("deltaX", 0), msg.get("deltaY", 0)
                    await _forward_scroll(platform, x, y, delta_x, delta_y)
                elif msg_type == "navigate":
                    # 支持导航到指定URL
                    nav_url = msg.get("url", "")
                    if nav_url:
                        crawler = _get_crawler(platform)
                        result = await crawler.navigate_to(nav_url)
                        await websocket.send_text(json.dumps({"type": "navigate_result", **result}))
                elif msg_type == "close":
                    break
            except (json.JSONDecodeError, TypeError):
                pass

    except WebSocketDisconnect:
        logger.info(f"浏览器预览 WebSocket 断开: platform={platform}")
    except Exception as e:
        logger.error(f"浏览器预览 WebSocket 错误: {type(e).__name__}: {e}")
    finally:
        if platform:
            active_preview_platforms.discard(platform)
        if stream_task:
            stream_task.cancel()


def _get_crawler(platform: str):
    """获取对应平台的爬虫实例"""
    if platform == "qq":
        from app.services.qq_crawler import qq_crawler
        return qq_crawler
    else:
        from app.services.xhs_crawler import xhs_crawler
        return xhs_crawler


async def _forward_click(platform: str, x: float, y: float):
    """将前端的点击事件转发到 Playwright 浏览器页面"""
    try:
        crawler = _get_crawler(platform)
        ctx = crawler._browser_context
        if not ctx:
            return  # 浏览器已关闭，静默忽略
        page = ctx.get("page")
        if not page:
            return
        await page.mouse.click(x, y)
        logger.debug(f"转发点击到 {platform} 浏览器: ({x}, {y})")
    except Exception as e:
        err_msg = str(e)
        if "closed" in err_msg or "disposed" in err_msg:
            pass  # 浏览器已关闭，静默忽略
        else:
            logger.debug(f"转发点击失败: {e}")


async def _forward_key(platform: str, key: str, text: str = ""):
    """将前端的键盘按键转发到 Playwright 浏览器页面"""
    try:
        crawler = _get_crawler(platform)
        ctx = crawler._browser_context
        if not ctx:
            return
        page = ctx.get("page")
        if not page:
            return
        # Playwright key名映射：常用特殊键
        key_map = {
            "Enter": "Enter", "Backspace": "Backspace", "Tab": "Tab",
            "Escape": "Escape", "ArrowUp": "ArrowUp", "ArrowDown": "ArrowDown",
            "ArrowLeft": "ArrowLeft", "ArrowRight": "ArrowRight",
            "Delete": "Delete", "Home": "Home", "End": "End",
            "PageUp": "PageUp", "PageDown": "PageDown",
        }
        if key in key_map:
            await page.keyboard.press(key_map[key])
        elif len(key) == 1:
            # 可打印字符直接 type
            await page.keyboard.type(key)
        elif text:
            await page.keyboard.type(text)
        logger.debug(f"转发按键到 {platform} 浏览器: key={key}")
    except Exception as e:
        err_msg = str(e)
        if "closed" not in err_msg and "disposed" not in err_msg:
            logger.debug(f"转发按键失败: {e}")


async def _forward_type_text(platform: str, text: str):
    """将批量文字输入转发到 Playwright（适用于验证码等场景）"""
    try:
        crawler = _get_crawler(platform)
        ctx = crawler._browser_context
        if not ctx:
            return
        page = ctx.get("page")
        if not page:
            return
        await page.keyboard.type(text, delay=50)
        logger.debug(f"转发文字输入到 {platform} 浏览器: text={text[:20]}")
    except Exception as e:
        err_msg = str(e)
        if "closed" not in err_msg and "disposed" not in err_msg:
            logger.debug(f"转发文字输入失败: {e}")


async def _forward_scroll(platform: str, x: float, y: float, delta_x: float, delta_y: float):
    """将滚动事件转发到 Playwright 浏览器页面"""
    try:
        crawler = _get_crawler(platform)
        ctx = crawler._browser_context
        if not ctx:
            return
        page = ctx.get("page")
        if not page:
            return
        await page.mouse.wheel(delta_x, delta_y)
        logger.debug(f"转发滚动到 {platform} 浏览器: ({delta_x}, {delta_y})")
    except Exception as e:
        err_msg = str(e)
        if "closed" not in err_msg and "disposed" not in err_msg:
            logger.debug(f"转发滚动失败: {e}")

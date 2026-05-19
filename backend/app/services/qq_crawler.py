import logging
import asyncio
import uuid
import json
import re
import base64
import hashlib
import aiohttp
import ssl
import time
import os
import urllib.parse
from typing import Optional
from datetime import datetime
from app.config import settings

logger = logging.getLogger(__name__)


def compute_g_tk(p_skey: str) -> int:
    """根据 p_skey 计算 g_tk（QQ空间API鉴权token）"""
    h = 5381
    for c in p_skey:
        h += (h << 5) + ord(c)
    return h & 0x7FFFFFFF


class QQCrawler:
    def __init__(self):
        self._status = {"running": False, "task_id": None, "progress": "", "error": None, "details": []}
        self._crawl_lock = asyncio.Lock()
        self._active_task: asyncio.Task | None = None
        self._max_api_pages = max(1, int(os.getenv("QQ_CRAWL_MAX_PAGES", "150")))
        self.login_status = "unknown"
        self.login_status_detail = ""
        self._browser_context = None  # Playwright context for QR login
        self._poll_task = None  # 轮询任务引用
        self._login_debug = {
            "updated_at": None,
            "platform": "qq",
            "url": "",
            "title": "",
            "status": "",
            "detail": "",
            "screenshot": "",
            "events": [],
        }

    def get_status(self) -> dict:
        return self._status

    def _log(self, msg: str, level: str = "info"):
        """同时记录到logger和status.details中"""
        getattr(logger, level, logger.info)(msg)
        self._status.setdefault("details", []).append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "msg": msg,
        })
        if len(self._status["details"]) > 50:
            self._status["details"] = self._status["details"][-50:]

    def _debug_event(self, message: str):
        logger.info(f"[QQ-DEBUG] {message}")
        self._login_debug["events"].append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "msg": message,
        })
        if len(self._login_debug["events"]) > 40:
            self._login_debug["events"] = self._login_debug["events"][-40:]

    async def _update_login_debug_snapshot(self, note: str = ""):
        self._login_debug["updated_at"] = datetime.now().isoformat()
        self._login_debug["status"] = self.login_status
        self._login_debug["detail"] = self.login_status_detail
        if note:
            self._debug_event(note)

        if not self._browser_context:
            return

        page = self._browser_context.get("page")
        if not page:
            return

        try:
            self._login_debug["url"] = page.url or ""
        except Exception:
            pass

        try:
            self._login_debug["title"] = await page.title()
        except Exception:
            pass

        try:
            shot = await page.screenshot(type="jpeg", quality=55, timeout=5000)
            self._login_debug["screenshot"] = base64.b64encode(shot).decode()
        except Exception:
            pass

    async def get_login_debug_view(self) -> dict:
        await self._update_login_debug_snapshot()
        return self._login_debug

    async def get_live_frame(self) -> dict | None:
        """获取当前浏览器页面的实时截图帧（供 WebSocket 流式推送）。
        返回 dict 包含 screenshot(base64), url, title, status, detail；
        浏览器未启动时返回 None。
        """
        if not self._browser_context:
            return None

        page = self._browser_context.get("page")
        if not page:
            return None

        frame: dict = {
            "status": self.login_status,
            "detail": self.login_status_detail,
            "url": "",
            "title": "",
            "screenshot": "",
        }

        try:
            frame["url"] = page.url or ""
        except Exception:
            pass

        try:
            frame["title"] = await page.title()
        except Exception:
            pass

        try:
            # 使用CDP直接截图，绕过Playwright的frame同步等待（QQ iframe多导致screenshot超时）
            cdp = await page.context.new_cdp_session(page)
            result = await cdp.send("Page.captureScreenshot", {
                "format": "jpeg", "quality": 50
            })
            await cdp.detach()
            frame["screenshot"] = result["data"]
        except Exception as e:
            # CDP失败时尝试常规截图
            try:
                shot = await page.screenshot(type="jpeg", quality=30, full_page=False, timeout=5000)
                frame["screenshot"] = base64.b64encode(shot).decode()
            except Exception:
                logger.debug(f"QQ截图完全失败: {type(e).__name__}: {e}")

        # 即使没有截图也返回frame（让前端显示状态信息而不是"等待浏览器启动"）
        return frame

    async def navigate_to(self, url: str) -> dict:
        """导航浏览器到指定URL"""
        def is_allowed_navigation_url(candidate_url: str) -> bool:
            parsed = urllib.parse.urlparse(candidate_url)
            hostname = (parsed.hostname or "").lower()
            allowed_suffixes = ("qq.com", "qzone.qq.com", "qpic.cn", "qlogo.cn", "gtimg.cn", "gtimg.com")
            return parsed.scheme in {"http", "https"} and any(
                hostname == suffix or hostname.endswith(f".{suffix}") for suffix in allowed_suffixes
            )

        if not is_allowed_navigation_url(url):
            return {"ok": False, "error": "仅允许导航到 QQ/Qzone 相关域名"}
        if not self._browser_context:
            return {"ok": False, "error": "浏览器未启动"}
        page = self._browser_context.get("page")
        if not page:
            return {"ok": False, "error": "页面不可用"}
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            if not is_allowed_navigation_url(page.url):
                await page.goto("https://user.qzone.qq.com/", wait_until="domcontentloaded", timeout=8000)
                return {"ok": False, "url": page.url, "error": "导航被重定向到非允许域名，已中止"}
            return {"ok": True, "url": page.url}
        except Exception as e:
            try:
                if page.url and not is_allowed_navigation_url(page.url):
                    await page.goto("https://user.qzone.qq.com/", wait_until="domcontentloaded", timeout=8000)
            except Exception:
                pass
            logger.warning(f"QQ导航失败: {e}")
            return {"ok": False, "url": page.url, "error": str(e)}

    # ===================== QQ空间 Web 扫码登录 =====================

    async def _cleanup_browser(self):
        """安全清理浏览器资源"""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            self._poll_task = None
        if self._browser_context:
            try:
                await self._browser_context["browser"].close()
            except Exception:
                pass
            try:
                await self._browser_context["playwright"].stop()
            except Exception:
                pass
            self._browser_context = None

    async def get_login_qrcode(self) -> Optional[str]:
        """通过Playwright获取QQ空间登录二维码（base64）"""
        # 先清理上一次的浏览器实例和轮询任务
        await self._cleanup_browser()
        self.login_status = "getting_qrcode"
        self.login_status_detail = "正在初始化浏览器..."
        await self._update_login_debug_snapshot("开始获取QQ二维码")

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("Playwright未安装，无法获取QQ二维码。请运行: pip install playwright && playwright install chromium")
            self.login_status = "error"
            self.login_status_detail = "Playwright未安装"
            await self._update_login_debug_snapshot("Playwright未安装")
            return None

        try:
            p = await async_playwright().start()
            logger.info("Playwright已启动")
        except Exception as e:
            logger.error(f"Playwright启动失败: {e}")
            self.login_status = "error"
            self.login_status_detail = f"Playwright启动失败: {e}"
            await self._update_login_debug_snapshot("Playwright启动失败")
            return None

        try:
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
            )
            logger.info("Chromium浏览器已启动")
        except Exception as e:
            logger.error(f"Chromium启动失败（可能缺少系统依赖）: {type(e).__name__}: {e}", exc_info=True)
            logger.error("如果在Linux上，请运行: playwright install-deps chromium")
            self.login_status = "error"
            self.login_status_detail = f"Chromium启动失败: {e}"
            await self._update_login_debug_snapshot("Chromium启动失败")
            try:
                await p.stop()
            except Exception:
                pass
            return None

        try:
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1024, "height": 768},
            )
            page = await context.new_page()

            # 清除所有残留cookie，防止上次登录session干扰导致状态误判（如"已扫码"）
            try:
                await context.clear_cookies()
                logger.debug("已清除浏览器cookie")
            except Exception:
                pass

            # 阻止字体加载（防止headless模式下screenshot因"waiting for fonts to load"超时）
            async def _abort_route(route):
                await route.abort()
            await page.route(re.compile(r"\.(woff2?|ttf|otf|eot)(\?|$)", re.IGNORECASE), _abort_route)

            # 打开QQ空间登录页
            try:
                await page.goto("https://qzone.qq.com", wait_until="domcontentloaded", timeout=20000)
            except Exception:
                logger.warning("QQ空间页面加载超时，尝试继续...")
            await asyncio.sleep(3)

            # 找到登录iframe
            qr_frame = page
            for frame in page.frames:
                if "xui.ptlogin2" in (frame.url or ""):
                    qr_frame = frame
                    logger.info(f"找到QQ登录iframe: {frame.url}")
                    break

            # 尝试点击"二维码登录"切换
            try:
                qr_switch = qr_frame.locator('#qlogin_list, .qrlogin, [id*="qr"], img[src*="qrcode"]')
                if await qr_switch.count() > 0:
                    await qr_switch.first.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

            # 获取二维码图片
            qr_img = qr_frame.locator('#qrlogin_img, .qrlogin_img, img[src*="ptqrshow"], img[id*="qrlogin"]')
            if await qr_img.count() == 0:
                qr_img = qr_frame.locator('.qrlogin, #login_div, .login_content, #login')

            # ⚠ 关键：不能用 aiohttp 下载 ptqrshow URL！
            # 每次请求 ptqrshow 会生成新的 QR session（新 qrsig cookie），
            # 导致展示给用户的二维码和浏览器中轮询的二维码不是同一个。
            # 必须从浏览器内部获取已渲染的二维码图像。
            qrcode_b64 = None
            if await qr_img.count() > 0:
                # 方法1: 在 iframe 内用 canvas 提取已渲染的二维码（保证与浏览器 session 一致）
                try:
                    qr_src = await qr_img.first.get_attribute('src')
                    if qr_src and 'ptqrshow' in qr_src:
                        logger.info(f"检测到QQ二维码: {qr_src[:80]}...")
                        # 等待图片完全加载
                        await asyncio.sleep(1)
                        # 在 iframe 上下文中用 canvas 读取已加载的图片像素
                        qrcode_b64 = await qr_frame.evaluate("""() => {
                            try {
                                const imgs = document.querySelectorAll('img[src*="ptqrshow"]');
                                for (const img of imgs) {
                                    if (!img.complete || img.naturalWidth === 0) continue;
                                    const canvas = document.createElement('canvas');
                                    canvas.width = img.naturalWidth;
                                    canvas.height = img.naturalHeight;
                                    const ctx = canvas.getContext('2d');
                                    ctx.drawImage(img, 0, 0);
                                    const dataUrl = canvas.toDataURL('image/png');
                                    return dataUrl.split(',')[1] || '';
                                }
                            } catch(e) {}
                            return '';
                        }""")
                        if qrcode_b64:
                            logger.info(f"成功通过canvas提取QQ二维码（与浏览器session一致）")
                        else:
                            logger.warning("canvas提取二维码返回空，图片可能未加载完成")
                except Exception as e_canvas:
                    logger.warning(f"canvas方式获取二维码失败: {e_canvas}")

                # 方法2: 元素截图（同样从浏览器获取，保证一致性）
                if not qrcode_b64:
                    try:
                        screenshot = await qr_img.first.screenshot(timeout=10000)
                        qrcode_b64 = base64.b64encode(screenshot).decode()
                        logger.info("通过元素截图获取QQ二维码")
                    except Exception as e_scr:
                        logger.warning(f"元素截图失败: {e_scr}")

            # 方法3: CDP截图兜底
            if not qrcode_b64:
                try:
                    cdp = await page.context.new_cdp_session(page)
                    result = await cdp.send("Page.captureScreenshot", {"format": "png"})
                    await cdp.detach()
                    qrcode_b64 = result["data"]
                    logger.info("使用CDP全页面截图兜底获取QQ二维码")
                except Exception:
                    screenshot = await page.screenshot(timeout=15000)
                    qrcode_b64 = base64.b64encode(screenshot).decode()
                    logger.info("使用Playwright全页面截图兜底获取QQ二维码")

            self._browser_context = {"browser": browser, "context": context, "page": page, "playwright": p}
            self.login_status = "waiting_scan"
            self.login_status_detail = "等待扫码"
            await self._update_login_debug_snapshot("二维码已生成，等待用户扫码")
            self._poll_task = asyncio.create_task(self._poll_login_status())
            return qrcode_b64

        except Exception as e:
            logger.error(f"获取QQ空间登录二维码失败: {type(e).__name__}: {e}", exc_info=True)
            self.login_status = "error"
            self.login_status_detail = f"获取二维码失败: {e}"
            await self._update_login_debug_snapshot(f"获取二维码失败: {type(e).__name__}")
            # 清理可能half-created的资源
            try:
                await browser.close()
            except Exception:
                pass
            try:
                await p.stop()
            except Exception:
                pass
            return None

    async def _poll_login_status(self):
        """轮询QQ空间登录状态（通过拦截iframe网络请求检测登录成功）
        
        现代Chrome禁用document.domain setter，ptlogin2 iframe无法调用parent的
        ptuiCB回调，页面不会自动跳转。因此直接拦截ptqrlogin网络响应获取认证URL，
        手动导航完成登录流程。
        """
        if not self._browser_context:
            return

        page = self._browser_context["page"]
        context = self._browser_context["context"]
        consecutive_errors = 0
        auth_url = None

        # 拦截所有 ptqrlogin 网络响应（包括 iframe 自动轮询的）
        latest_network_state = ""  # 来自网络拦截的最新状态
        network_poll_count = 0

        async def _on_response(response):
            nonlocal auth_url, latest_network_state, network_poll_count
            try:
                if "ptqrlogin" not in response.url or response.status != 200:
                    return
                network_poll_count += 1
                body = await response.text()
                state_match = re.search(r"ptuiCB\('(\d+)'", body)
                state = state_match.group(1) if state_match else "?"

                # 状态变更时记录日志
                if state != latest_network_state:
                    logger.info(f"QQ ptqrlogin 状态变更: '{latest_network_state}' → '{state}' (第{network_poll_count}次网络响应)")
                    latest_network_state = state

                if state == "0":
                    url_match = re.search(r"ptuiCB\('0','\d+','(https?://[^']+)'", body)
                    if url_match:
                        auth_url = url_match.group(1)
                        logger.info("QQ登录: 网络拦截到认证URL（用户已扫码确认）")
                        self.login_status = "waiting_scan"
                        self.login_status_detail = "已扫码确认，正在完成登录..."
                        await self._update_login_debug_snapshot("拦截到认证URL")
                elif state == "66":
                    self.login_status_detail = "等待扫码"
                elif state == "67":
                    self.login_status = "waiting_scan"
                    self.login_status_detail = "已扫码，等待手机确认"
                    await self._update_login_debug_snapshot("已扫码，等待手机确认")
                elif state == "65":
                    self.login_status = "expired"
                    self.login_status_detail = "二维码已过期"
            except Exception as e:
                logger.debug(f"处理ptqrlogin响应异常: {e}")

        # 同时拦截请求，记录 iframe 自动轮询使用的 ptqrtoken（用于诊断）
        async def _on_request(request):
            if "ptqrlogin" in request.url:
                try:
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(request.url).query)
                    token = parsed.get("ptqrtoken", ["?"])[0]
                    if network_poll_count <= 1:  # 只记录前几次
                        logger.info(f"QQ iframe自动轮询请求: ptqrtoken={token}")
                except Exception:
                    pass

        page.on("response", _on_response)
        page.on("request", _on_request)

        for i in range(60):  # 最多轮询3分钟
            await asyncio.sleep(3)
            try:
                if not self._browser_context:
                    logger.info("QQ登录轮询: 浏览器已被清理，停止轮询")
                    return

                # 从网络拦截更新状态（iframe 自动轮询的结果）
                if latest_network_state == "65":
                    # 在判定过期前，最后检查一次cookie是否已经有登录态
                    # （可能扫码成功但state=0被漏掉，cookie已经设置）
                    try:
                        last_check_cookies = await context.cookies()
                        has_login_now = any(
                            c["name"] in ("p_skey", "pt_key", "skey", "pt4_token") and c.get("value")
                            for c in last_check_cookies
                        )
                        if has_login_now:
                            logger.info("QQ登录: 网络状态=65但检测到登录Cookie，判定登录成功")
                            await self._save_login_cookies(last_check_cookies)
                            self.login_status = "logged_in"
                            self.login_status_detail = "登录成功，Cookie已保存"
                            await self._update_login_debug_snapshot("网络65但cookie检测登录成功")
                            break
                    except Exception:
                        pass
                    self.login_status = "expired"
                    self.login_status_detail = "二维码已过期，请刷新重试"
                    logger.info("QQ登录二维码已失效（网络拦截状态=65）")
                    await self._update_login_debug_snapshot("二维码已过期")
                    break

                # ---- 方法1: 拦截到认证URL → 手动导航获取cookie ----
                if auth_url:
                    logger.info("QQ登录: 手动导航认证URL完成登录流程...")
                    try:
                        await page.goto(auth_url, wait_until="domcontentloaded", timeout=15000)
                    except Exception:
                        pass  # 重定向链可能超时但cookie已设置
                    await asyncio.sleep(2)

                    cookies = await context.cookies()
                    login_cookie_names = ("p_skey", "pt_key", "skey", "pt4_token")
                    has_login_cookie = any(
                        c["name"] in login_cookie_names and c.get("value")
                        for c in cookies
                    )
                    if has_login_cookie:
                        # 登录成功后，先导航到QQ空间获取p_skey
                        logger.info("QQ空间登录成功（通过拦截认证URL），正在获取p_skey...")
                        has_p_skey = any(c["name"] == "p_skey" and c.get("value") for c in cookies)
                        if not has_p_skey:
                            for qz_url in [
                                "https://user.qzone.qq.com",
                                "https://qzone.qq.com",
                                "https://h5.qzone.qq.com/mqzone/index",
                            ]:
                                try:
                                    await page.goto(qz_url, wait_until="domcontentloaded", timeout=15000)
                                    await asyncio.sleep(3)
                                    cookies = await context.cookies()
                                    has_p_skey = any(c["name"] == "p_skey" and c.get("value") for c in cookies)
                                    if has_p_skey:
                                        logger.info(f"登录后从 {qz_url} 成功获取p_skey")
                                        break
                                except Exception as e:
                                    logger.debug(f"登录后导航到 {qz_url} 失败: {e}")
                        await self._save_login_cookies(cookies)
                        self.login_status = "logged_in"
                        self.login_status_detail = "登录成功，Cookie已保存"
                        await self._update_login_debug_snapshot("认证URL流程登录成功")
                        break

                    # 有时需要再次导航到qzone获取完整cookie
                    try:
                        await page.goto("https://user.qzone.qq.com", wait_until="domcontentloaded", timeout=10000)
                        await asyncio.sleep(3)
                        cookies = await context.cookies()
                        has_login_cookie = any(
                            c["name"] in login_cookie_names and c.get("value")
                            for c in cookies
                        )
                        if has_login_cookie:
                            logger.info("QQ空间登录成功（认证URL + 重导航）！")
                            await self._save_login_cookies(cookies)
                            self.login_status = "logged_in"
                            self.login_status_detail = "登录成功，Cookie已保存"
                            await self._update_login_debug_snapshot("认证URL+重导航登录成功")
                            break
                    except Exception:
                        pass

                    logger.warning("认证URL后仍未获取cookie，继续等待...")
                    self.login_status_detail = "已扫码但未获取到登录Cookie，请在手机端确认授权"
                    await self._update_login_debug_snapshot("认证URL后未获取到Cookie")
                    auth_url = None  # 重置，等下一轮

                # ---- 方法1.5: 主动轮询 ptqrlogin ----
                # 仅当 iframe 自动轮询未检测到时才手动轮询（避免重复请求）
                if network_poll_count > 0 and latest_network_state in ("66", "67"):
                    # iframe 自动轮询正在工作，无需手动
                    if latest_network_state == "67":
                        self.login_status_detail = "已扫码，等待手机确认"
                    else:
                        self.login_status_detail = "等待扫码"
                    # 仍然检查 cookie 有无变化
                    cookies = await context.cookies()
                    has_login_cookie = any(
                        c["name"] in ("p_skey", "pt_key", "skey", "pt4_token") and c.get("value")
                        for c in cookies
                    )
                    if has_login_cookie:
                        logger.info(f"QQ空间登录成功（通过cookie检测），正在获取p_skey...")
                        # 导航到QQ空间获取p_skey
                        has_p_skey = any(c["name"] == "p_skey" and c.get("value") for c in cookies)
                        if not has_p_skey:
                            for qz_url in [
                                "https://user.qzone.qq.com",
                                "https://qzone.qq.com",
                                "https://h5.qzone.qq.com/mqzone/index",
                            ]:
                                try:
                                    await page.goto(qz_url, wait_until="domcontentloaded", timeout=15000)
                                    await asyncio.sleep(3)
                                    cookies = await context.cookies()
                                    has_p_skey = any(c["name"] == "p_skey" and c.get("value") for c in cookies)
                                    if has_p_skey:
                                        logger.info(f"登录后从 {qz_url} 成功获取p_skey")
                                        break
                                except Exception as e:
                                    logger.debug(f"登录后导航到 {qz_url} 失败: {e}")
                        await self._save_login_cookies(cookies)
                        self.login_status = "logged_in"
                        self.login_status_detail = "登录成功，Cookie已保存"
                        await self._update_login_debug_snapshot("cookie检测登录成功")
                        break
                    continue

                # ---- 方法1.5: 主动轮询 ptqrlogin ----
                # 完全在 iframe JS 上下文中执行：读取 iframe 的 cookie → 计算 ptqrtoken → fetch
                # 这样 cookie/ptqrtoken 严格一致，避免 Python context.cookies() 与 iframe 实际 cookie 不一致
                login_frame = None
                for frame in page.frames:
                    if "xui.ptlogin2" in (frame.url or ""):
                        login_frame = frame
                        break

                if not login_frame:
                    if i % 5 == 0:
                        self._debug_event("未找到ptlogin2 iframe，等待...")
                    continue

                # 从 context.cookies() 获取 pt_login_sig 作为备用（可能是 HttpOnly cookie，JS 无法读取）
                cookies_now = await context.cookies()
                backup_login_sig = ""
                for c in cookies_now:
                    if c.get("name") == "pt_login_sig" and c.get("value"):
                        backup_login_sig = c["value"]
                        break

                try:
                    poll_result = await login_frame.evaluate("""async (backupLoginSig) => {
                        try {
                            const allCookies = document.cookie;
                            const qrsigM = allCookies.match(/qrsig=([^;\\s]+)/);
                            const sigM = allCookies.match(/pt_login_sig=([^;\\s]+)/);
                            
                            if (!qrsigM) return {error: 'no_qrsig', cookies: allCookies.substring(0, 300)};
                            
                            const qrsig = qrsigM[1];
                            const loginSig = sigM ? sigM[1] : (backupLoginSig || '');
                            
                            // QQ 标准 ptqrtoken 哈希算法
                            var e = 0;
                            for (var i = 0; i < qrsig.length; i++) {
                                e += (e << 5) + qrsig.charCodeAt(i);
                            }
                            const ptqrtoken = 2147483647 & e;
                            
                            const params = new URLSearchParams({
                                u1: 'https://qzs.qq.com/qzone/v5/loginsucc.html?para=izone',
                                ptqrtoken: String(ptqrtoken),
                                ptredirect: '0', h: '1', t: '1', g: '1',
                                from_ui: '1', ptlang: '2052',
                                action: '0-0-' + Date.now(),
                                js_ver: '22080914', js_type: '1',
                                login_sig: loginSig,
                                pt_uistyle: '40', aid: '549000912',
                                daid: '5', has_onekey: '1'
                            });
                            
                            // 直接使用 iframe 的 location.host 确保域名正确
                            const url = location.protocol + '//' + location.host + '/ssl/ptqrlogin?' + params.toString();
                            const resp = await fetch(url, {credentials: 'include'});
                            const text = await resp.text();
                            return {
                                ok: true, text: text,
                                ptqrtoken: ptqrtoken,
                                qrsig_head: qrsig.substring(0, 25),
                                has_login_sig: !!loginSig,
                                iframe_host: location.host
                            };
                        } catch(e) {
                            return {error: e.message};
                        }
                    }""", backup_login_sig)

                    poll_text = ""
                    poll_source = "iframe_internal"
                    if isinstance(poll_result, dict) and poll_result.get("ok"):
                        poll_text = poll_result.get("text", "")
                        if i <= 1:
                            logger.info(
                                f"QQ ptqrlogin [iframe_internal] host={poll_result.get('iframe_host')} "
                                f"ptqrtoken={poll_result.get('ptqrtoken')} "
                                f"qrsig={poll_result.get('qrsig_head')}... "
                                f"has_login_sig={poll_result.get('has_login_sig')}"
                            )
                    elif isinstance(poll_result, dict):
                        if i <= 1:
                            logger.warning(f"QQ iframe内部轮询失败: {poll_result}")
                        # iframe无法读取qrsig（可能是HttpOnly），尝试 Python 构建 URL 兜底
                        cookies_map = {c.get("name", ""): c.get("value", "") for c in cookies_now}
                        qrsig = cookies_map.get("qrsig", "")
                        if qrsig:
                            try:
                                ptqrtoken = await page.evaluate("""(qrsig) => {
                                    var e = 0;
                                    for (var i = 0; i < qrsig.length; i++) {
                                        e += (e << 5) + qrsig.charCodeAt(i);
                                    }
                                    return 2147483647 & e;
                                }""", qrsig)
                                action = f"0-0-{int(time.time() * 1000)}"
                                params = {
                                    "u1": "https://qzs.qq.com/qzone/v5/loginsucc.html?para=izone",
                                    "ptqrtoken": str(ptqrtoken),
                                    "ptredirect": "0", "h": "1", "t": "1", "g": "1",
                                    "from_ui": "1", "ptlang": "2052", "action": action,
                                    "js_ver": "22080914", "js_type": "1",
                                    "login_sig": cookies_map.get("pt_login_sig", ""),
                                    "pt_uistyle": "40", "aid": "549000912", "daid": "5", "has_onekey": "1",
                                }
                                query = urllib.parse.urlencode(params, safe=":/?")
                                ptqrlogin_url = f"https://xui.ptlogin2.qq.com/ssl/ptqrlogin?{query}"
                                poll_text = await login_frame.evaluate("""async (url) => {
                                    const resp = await fetch(url, { credentials: 'include' });
                                    return await resp.text();
                                }""", ptqrlogin_url)
                                poll_source = "iframe_fetch_fallback"
                                if i <= 1:
                                    logger.info(f"QQ ptqrlogin [fallback] ptqrtoken={ptqrtoken} qrsig={qrsig[:20]}...")
                            except Exception as e_fb:
                                logger.debug(f"QQ iframe fetch兜底失败: {e_fb}")

                    if not poll_text:
                        if i % 3 == 0:
                            self._debug_event("ptqrlogin轮询无响应")
                        continue

                    # 解析状态
                    status_match = re.search(r"ptuiCB\('([^']+)'", poll_text)
                    qq_state = status_match.group(1) if status_match else ""

                    if i <= 1:
                        logger.info(f"QQ ptqrlogin响应 [source={poll_source}] state={qq_state}")
                        logger.debug(f"QQ ptqrlogin原始响应: {poll_text[:200]}")

                    if qq_state == "0":
                        url_match = re.search(r"ptuiCB\('0','\d+','(https?://[^']+)'", poll_text)
                        if url_match:
                            auth_url = url_match.group(1)
                            logger.info("QQ登录: 主动轮询检测到扫码确认，准备完成认证")
                            self.login_status = "waiting_scan"
                            self.login_status_detail = "已扫码确认，正在完成登录..."
                            await self._update_login_debug_snapshot("轮询检测到扫码确认")
                    elif qq_state == "66":
                        self.login_status = "waiting_scan"
                        self.login_status_detail = "等待扫码"
                    elif qq_state == "67":
                        self.login_status = "waiting_scan"
                        self.login_status_detail = "已扫码，等待手机确认"
                        if i % 2 == 0:
                            await self._update_login_debug_snapshot("已扫码，等待手机确认")
                    elif qq_state == "65":
                        self.login_status = "expired"
                        self.login_status_detail = "二维码已过期，请刷新重试"
                        logger.info("QQ登录二维码已失效（ptqrlogin状态=65）")
                        await self._update_login_debug_snapshot("二维码已过期")
                        break
                    elif qq_state:
                        self.login_status = "waiting_scan"
                        self.login_status_detail = f"QQ登录状态码: {qq_state}"
                        self._debug_event(f"ptqrlogin返回状态码={qq_state}")
                    else:
                        if i % 3 == 0:
                            self._debug_event(f"ptqrlogin无状态响应[{poll_source}]: {poll_text[:120]}")
                except Exception as e:
                    if i <= 2:
                        logger.debug(f"QQ ptqrlogin轮询异常: {type(e).__name__}: {e}")

                # ---- 方法2: 常规检查（兼容document.domain正常工作的环境） ----
                url = page.url
                cookies = await context.cookies()
                consecutive_errors = 0

                has_login_cookie = any(
                    c["name"] in ("p_skey", "pt_key", "skey", "pt4_token") and c.get("value")
                    for c in cookies
                )
                url_success = "user.qzone.qq.com" in url and "login" not in url.lower()

                if has_login_cookie or url_success:
                    logger.info(f"QQ空间登录成功！URL={url[:80]}")
                    await self._save_login_cookies(cookies)
                    self.login_status = "logged_in"
                    self.login_status_detail = "登录成功，Cookie已保存"
                    await self._update_login_debug_snapshot("常规检查判定登录成功")
                    break

            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    logger.warning(f"QQ登录轮询连续{consecutive_errors}次异常，停止: {e}")
                    if self.login_status == "waiting_scan":
                        self.login_status = "expired"
                        self.login_status_detail = f"轮询异常: {e}"
                        await self._update_login_debug_snapshot("轮询异常达到上限，终止")
                    break
        else:
            if self.login_status == "waiting_scan":
                self.login_status = "expired"
                self.login_status_detail = "登录超时（3分钟未完成）"
                logger.info("QQ登录二维码已过期（3分钟未扫描）")
                await self._update_login_debug_snapshot("登录超时")

        try:
            page.remove_listener("response", _on_response)
            page.remove_listener("request", _on_request)
        except Exception:
            pass
        # 登录成功时，保持浏览器供预览窗口使用
        if self.login_status == "logged_in":
            from app.routers.ws import active_preview_platforms
            logger.info("QQ登录成功，等待预览窗口断开后关闭浏览器（最长5分钟）")
            for _ in range(60):  # 最长5分钟
                await asyncio.sleep(5)
                if "qq" not in active_preview_platforms:
                    logger.info("QQ预览窗口已断开，5秒后关闭浏览器")
                    await asyncio.sleep(5)
                    break
        await self._cleanup_browser()

    async def _save_login_cookies(
        self,
        cookies: list[dict],
        login_account_id: int | None = None,
    ):
        """将QQ空间登录Cookie保存到数据库Account表。确保包含p_skey。"""
        from app.database import async_session
        from app.models.account import Account
        from app.services.account_risk_service import mark_account_verified
        from sqlalchemy import select

        # 检查是否包含p_skey（QQ空间API必须的cookie）
        has_p_skey = any(c["name"] == "p_skey" and c.get("value") for c in cookies)
        if not has_p_skey:
            logger.warning("QQ登录Cookie缺少p_skey，尝试导航到QQ空间获取...")
            # 尝试通过浏览器导航获取p_skey
            # 注意: self._browser_context 是 dict {"browser":..., "context":..., "page":...}
            try:
                bc = self._browser_context
                if bc and isinstance(bc, dict):
                    pw_context = bc.get("context")
                    pw_page = bc.get("page")
                    if pw_context and pw_page:
                        for qzone_url in [
                            "https://user.qzone.qq.com",
                            "https://qzone.qq.com",
                            "https://h5.qzone.qq.com/mqzone/index",
                        ]:
                            try:
                                await pw_page.goto(qzone_url, wait_until="domcontentloaded", timeout=15000)
                                await asyncio.sleep(3)
                                cookies = await pw_context.cookies()
                                has_p_skey = any(c["name"] == "p_skey" and c.get("value") for c in cookies)
                                if has_p_skey:
                                    logger.info(f"成功从 {qzone_url} 获取p_skey")
                                    break
                            except Exception as e:
                                logger.debug(f"导航到 {qzone_url} 失败: {e}")
                                continue
                    else:
                        logger.warning("browser_context中缺少context或page")
                elif bc:
                    logger.warning(f"browser_context类型异常: {type(bc)}")
            except Exception as e:
                logger.warning(f"尝试获取p_skey失败: {e}")

        if not has_p_skey:
            logger.warning("警告：保存的Cookie中仍然缺少p_skey，QQ空间API可能无法使用")

        # 记录保存的关键cookie
        key_cookies = {c["name"]: c.get("value","")[:20] for c in cookies if c["name"] in ("p_skey", "skey", "uin", "pt_key")}
        logger.info(f"保存QQ Cookie, 关键字段: {list(key_cookies.keys())}")

        # 从cookie中提取QQ号
        qq_number = ""
        for c in cookies:
            if c["name"] == "uin" and c.get("value"):
                qq_number = c["value"].lstrip("o0")  # uin 格式如 o1234567890
                break
            if c["name"] == "ptcz" or c["name"] == "p_uin":
                val = c.get("value", "")
                if val.isdigit():
                    qq_number = val

        async with async_session() as db:
            account = None
            if login_account_id is not None:
                result = await db.execute(
                    select(Account).where(
                        Account.id == login_account_id,
                        Account.platform == "qq",
                        Account.is_target == 0,
                    )
                )
                account = result.scalars().first()

            if account is None and qq_number:
                result = await db.execute(
                    select(Account).where(
                        Account.platform == "qq",
                        Account.account_id == qq_number,
                        Account.is_target == 0,
                    )
                )
                account = result.scalars().first()

            if account is None:
                result = await db.execute(
                    select(Account)
                    .where(Account.platform == "qq", Account.is_target == 0)
                    .order_by(Account.last_login.desc(), Account.id.desc())
                )
                account = result.scalars().first()

            if account:
                account.cookies = json.dumps(cookies)
                account.last_login = datetime.now()
                if qq_number:
                    account.account_id = qq_number
                mark_account_verified(account)
            else:
                account = Account(
                    platform="qq",
                    account_id=qq_number or "qq_login",
                    cookies=json.dumps(cookies),
                    is_target=0,
                    last_login=datetime.now(),
                )
                mark_account_verified(account)
                db.add(account)
            await db.commit()
            logger.info(f"QQ空间登录Cookie已保存（QQ={qq_number or '未知'}）")

    # Cookie过期通知频率限制（平台 -> 上次通知时间）
    _cookie_notify_timestamps: dict = {}

    async def _notify_cookie_expired(self, platform: str, detail: str = ""):
        """Cookie过期时通过NapCat通知管理员。
        跳过条件：
        - 通知功能未开启
        - 该平台的登录账号已禁用（status != 'active'）
        - 1小时内已发送过同平台通知（防止误报轰炸）
        """
        try:
            from app.database import async_session
            from app.models.system_config import SystemConfig
            from app.models.account import Account
            from sqlalchemy import select

            # 频率限制：同平台1小时内只发一次
            now = datetime.now()
            last_notify = self._cookie_notify_timestamps.get(platform)
            if last_notify and (now - last_notify).total_seconds() < 3600:
                logger.debug(f"{platform}Cookie过期通知已在1小时内发送过，跳过")
                return

            async with async_session() as db:
                result = await db.execute(
                    select(SystemConfig).where(SystemConfig.key == "login_notify_enabled")
                )
                config = result.scalar_one_or_none()
                if not config or config.value != "true":
                    return

                # 检查该平台的登录账号状态，已禁用/expired的不发通知
                platform_key = "qq" if "QQ" in platform or "qq" in platform.lower() else "xhs"
                login_result = await db.execute(
                    select(Account).where(
                        Account.platform == platform_key,
                        Account.is_target == 0,
                    )
                )
                login_accounts = login_result.scalars().all()
                if login_accounts:
                    has_non_disabled = any(a.status != "disabled" for a in login_accounts)
                    if not has_non_disabled:
                        logger.debug(f"{platform}所有登录账号均已禁用，跳过过期通知")
                        return

                result = await db.execute(
                    select(SystemConfig).where(SystemConfig.key == "admin_qq")
                )
                admin_config = result.scalar_one_or_none()
                if not admin_config or not admin_config.value:
                    return

                admin_qq = admin_config.value

            from app.services.napcat_client import napcat_client
            time_str = now.strftime("%Y-%m-%d %H:%M:%S")
            msg = f"⚠️ {platform} Cookie已过期\n时间: {time_str}\n请尽快重新扫码登录，否则爬取任务将无法执行。"
            if detail:
                msg += f"\n详情: {detail}"
            await napcat_client.send_message(admin_qq, [{"type": "text", "content": msg}])
            self._cookie_notify_timestamps[platform] = now
            logger.info(f"已发送{platform}Cookie过期通知到管理员QQ={admin_qq}")
        except Exception as e:
            logger.debug(f"发送Cookie过期通知失败: {e}")

    async def auto_relogin_via_napcat(self) -> bool:
        """QQ空间Cookie过期时，自动生成登录二维码并通过NapCat推送到管理员QQ。
        
        流程：
        1. 生成QQ空间登录二维码
        2. 将二维码图片发送到管理员QQ
        3. 后台轮询等待扫码（poll_login_status已由get_login_qrcode启动）
        4. 用户扫码后自动保存Cookie
        
        返回True=已成功推送二维码，False=推送失败
        注意：受 login_notify_enabled 开关控制，关闭时不推送二维码。
        """
        logger.info("QQ空间Cookie过期，启动自动重新登录流程...")

        # 获取管理员QQ
        try:
            from app.database import async_session
            from app.models.system_config import SystemConfig
            from sqlalchemy import select

            admin_qq = None
            async with async_session() as db:
                # 检查通知开关，关闭时不推送二维码
                notify_result = await db.execute(
                    select(SystemConfig).where(SystemConfig.key == "login_notify_enabled")
                )
                notify_config = notify_result.scalar_one_or_none()
                if not notify_config or notify_config.value != "true":
                    logger.info("QQ自动重登: Cookie过期通知已关闭，跳过二维码推送")
                    return False

                result = await db.execute(
                    select(SystemConfig).where(SystemConfig.key == "admin_qq")
                )
                admin_config = result.scalar_one_or_none()
                if admin_config and admin_config.value:
                    admin_qq = admin_config.value

            if not admin_qq:
                logger.warning("QQ自动重登: 未配置管理员QQ，无法推送二维码")
                return False

            from app.services.napcat_client import napcat_client
            if not napcat_client.connections:
                logger.warning("QQ自动重登: NapCat未连接，无法推送二维码")
                return False

            # 生成QQ空间登录二维码
            qrcode_b64 = await self.get_login_qrcode()
            if not qrcode_b64:
                logger.error("QQ自动重登: 生成二维码失败")
                await napcat_client.send_message(admin_qq, [
                    {"type": "text", "content": "⚠️ QQ空间Cookie已过期，自动生成二维码失败，请手动登录。"}
                ])
                return False

            # 通过NapCat发送二维码图片和提示消息到管理员QQ
            time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await napcat_client.send_message(admin_qq, [
                {"type": "text", "content": f"⚠️ QQ空间Cookie已过期\n时间: {time_str}\n\n已自动生成登录二维码，请使用手机QQ扫描下方二维码重新登录。\n二维码有效期约3分钟。"},
                {"type": "image", "url": f"base64://{qrcode_b64}"},
            ])
            logger.info(f"QQ自动重登: 二维码已推送到管理员QQ={admin_qq}")

            # 等待登录完成（最长3.5分钟，poll_login_status 已在后台运行）
            for _ in range(42):  # 42 * 5s = 210s ≈ 3.5分钟
                await asyncio.sleep(5)
                if self.login_status == "logged_in":
                    logger.info("QQ自动重登: 扫码登录成功！Cookie已自动保存")
                    await napcat_client.send_message(admin_qq, [
                        {"type": "text", "content": "✅ QQ空间扫码登录成功！Cookie已自动保存。"}
                    ])
                    return True
                elif self.login_status in ("expired", "error"):
                    logger.warning(f"QQ自动重登: 登录失败 status={self.login_status}")
                    await napcat_client.send_message(admin_qq, [
                        {"type": "text", "content": f"❌ QQ空间二维码已过期/登录失败，请稍后在网页端重试。\n状态: {self.login_status_detail}"}
                    ])
                    return False

            # 超时
            logger.warning("QQ自动重登: 等待扫码超时")
            await napcat_client.send_message(admin_qq, [
                {"type": "text", "content": "⏰ QQ空间登录二维码已超时，请在网页端手动重新扫码登录。"}
            ])
            return False

        except Exception as e:
            logger.error(f"QQ自动重登失败: {type(e).__name__}: {e}", exc_info=True)
            return False

    # ===================== NapCat凭证自动登录 =====================

    async def login_via_napcat(self) -> dict:
        """通过NapCat插件获取QQ空间Cookie，实现免扫码自动登录。
        
        原理：NapCat基于NTQQ运行，已持有QQ登录态。通过已连接的NapCat插件
        调用OneBot11的get_cookies接口获取qzone.qq.com域的凭证（uin、p_skey、skey等），
        保存到数据库供爬虫使用。无需单独配置OneBot11 WS地址和Token。
        """
        from app.database import async_session
        from app.models.account import Account
        from app.services.napcat_client import napcat_client
        from sqlalchemy import select

        if not napcat_client.connections:
            return {"success": False, "message": "NapCat插件未连接，请确保NapCat已启动且airecordsandreminders插件已安装并启用"}

        logger.info("NapCat自动登录: 通过插件通道获取QQ空间Cookie...")

        try:
            # 通过插件WS通道请求QZone Cookie（插件内部调用OneBot11 API）
            result = await napcat_client.request(
                request_type="get_qzone_cookies",
                response_type="qzone_cookies_result",
                timeout=15,
            )

            if not result.get("success"):
                err = result.get("error", "未知错误")
                logger.warning(f"NapCat获取Cookie失败: {err}")
                return {"success": False, "message": f"NapCat获取Cookie失败: {err}"}

            login_info = result.get("login_info", {})
            cookies_data = result.get("cookies", {})
            credentials = result.get("credentials", {})

            cookies_str = cookies_data.get("cookies", "")
            if not cookies_str:
                return {"success": False, "message": "NapCat返回空Cookie，可能NapCat尚未完全登录"}

            qq_number = str(login_info.get("user_id", "")) or ""
            nickname = login_info.get("nickname", "")
            csrf_token = credentials.get("csrf_token", 0)
            logger.info(f"NapCat登录信息: QQ={qq_number}, 昵称={nickname}, csrf_token={csrf_token}")

            # 解析cookies字符串 "uin=xxx; skey=yyy; p_skey=zzz"
            parsed_cookies = []
            for part in cookies_str.split(";"):
                part = part.strip()
                if "=" in part:
                    name, value = part.split("=", 1)
                    name = name.strip()
                    value = value.strip()
                    if name and value:
                        domain = ".qq.com"
                        if name in ("p_skey",):
                            domain = ".qzone.qq.com"
                        parsed_cookies.append({
                            "name": name, "value": value,
                            "domain": domain, "path": "/",
                        })

            # 检查关键cookie
            cookie_names = {c["name"] for c in parsed_cookies}
            has_uin = "uin" in cookie_names or "p_uin" in cookie_names
            has_p_skey = "p_skey" in cookie_names
            has_skey = "skey" in cookie_names

            if not has_uin or not (has_p_skey or has_skey):
                missing = []
                if not has_uin: missing.append("uin")
                if not has_p_skey: missing.append("p_skey")
                if not has_skey: missing.append("skey")
                return {
                    "success": False,
                    "message": f"NapCat Cookie缺少关键字段: {', '.join(missing)}。Cookie名称: {sorted(cookie_names)}"
                }

            # 提取QQ号
            qq_number = str(login_info.get("user_id", "")) or ""
            if not qq_number:
                for c in parsed_cookies:
                    if c["name"] == "uin":
                        qq_number = c["value"].lstrip("o0")
                        break
            nickname = login_info.get("nickname", "")

            # 验证cookie有效性：用p_skey计算g_tk并测试API
            p_skey_val = ""
            skey_val = ""
            for c in parsed_cookies:
                if c["name"] == "p_skey": p_skey_val = c["value"]
                if c["name"] == "skey": skey_val = c["value"]

            token_key = p_skey_val or skey_val
            if token_key and qq_number:
                g_tk = compute_g_tk(token_key)
                verify_url = (
                    f"https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
                    f"?uin={qq_number}&ftype=0&sort=0&pos=0&num=1&g_tk={g_tk}&format=json"
                )
                try:
                    cookies_dict = {c["name"]: c["value"] for c in parsed_cookies}
                    import ssl as _ssl
                    ssl_ctx = _ssl.create_default_context()
                    ssl_ctx.check_hostname = False
                    ssl_ctx.verify_mode = _ssl.CERT_NONE
                    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
                    async with aiohttp.ClientSession(cookies=cookies_dict, connector=connector) as verify_session:
                        async with verify_session.get(verify_url, headers={
                            "Referer": "https://user.qzone.qq.com/",
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        }) as resp:
                            if resp.status == 200:
                                logger.info(f"NapCat Cookie验证成功 (HTTP 200, g_tk={g_tk})")
                            else:
                                logger.warning(f"NapCat Cookie验证返回HTTP {resp.status}，可能无效")
                except Exception as e:
                    logger.debug(f"NapCat Cookie验证请求异常: {e}")

            # 保存到数据库
            async with async_session() as db:
                from app.services.account_risk_service import mark_account_verified

                # 查找已有的NapCat登录账号（通过QQ号匹配）
                existing = None
                if qq_number:
                    result = await db.execute(
                        select(Account).where(
                            Account.platform == "qq",
                            Account.account_id == qq_number,
                            Account.is_target == 0,
                        )
                    )
                    existing = result.scalars().first()

                # 只精确匹配QQ号：找到则更新，找不到则新增（不覆盖其他账号）
                if existing:
                    existing.cookies = json.dumps(parsed_cookies)
                    existing.last_login = datetime.now()
                    if nickname:
                        existing.nickname = nickname
                    mark_account_verified(existing)
                    logger.info(f"NapCat登录: 更新已有QQ登录账号 id={existing.id}, QQ={qq_number}")
                else:
                    new_account = Account(
                        platform="qq",
                        account_id=qq_number or "napcat",
                        nickname=nickname,
                        cookies=json.dumps(parsed_cookies),
                        is_target=0,
                        last_login=datetime.now(),
                    )
                    mark_account_verified(new_account)
                    db.add(new_account)
                    logger.info(f"NapCat登录: 创建新QQ登录账号 QQ={qq_number}")

                await db.commit()

            self.login_status = "logged_in"
            self.login_status_detail = f"NapCat自动登录成功 (QQ={qq_number})"
            logger.info(f"NapCat自动登录成功: QQ={qq_number}, 昵称={nickname}, Cookie数={len(parsed_cookies)}, "
                        f"p_skey={'有' if has_p_skey else '无'}, skey={'有' if has_skey else '无'}")

            return {
                "success": True,
                "message": f"NapCat登录成功！QQ: {qq_number}" + (f" ({nickname})" if nickname else ""),
                "qq": qq_number,
                "nickname": nickname,
                "cookies_count": len(parsed_cookies),
                "has_p_skey": has_p_skey,
            }

        except asyncio.TimeoutError:
            return {"success": False, "message": "NapCat插件响应超时，请检查插件是否正常运行"}
        except RuntimeError as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            logger.error(f"NapCat自动登录异常: {type(e).__name__}: {e}", exc_info=True)
            return {"success": False, "message": f"NapCat登录失败: {type(e).__name__}: {e}"}

    # ===================== Cookie自动续期 =====================

    async def _load_login_account(
        self,
        login_account_id: int | None = None,
        *,
        require_cookies: bool = False,
        allow_degraded: bool = False,
    ):
        from app.database import async_session
        from app.models.account import Account
        from app.services.account_risk_service import get_preferred_login_account, is_login_account_eligible
        from sqlalchemy import select

        async with async_session() as db:
            if login_account_id is not None:
                result = await db.execute(
                    select(Account).where(
                        Account.id == login_account_id,
                        Account.platform == "qq",
                        Account.is_target == 0,
                    )
                )
                account = result.scalars().first()
                if account and is_login_account_eligible(
                    account,
                    require_cookies=require_cookies,
                    allow_degraded=allow_degraded,
                ):
                    return account
                return None

            return await get_preferred_login_account(
                db,
                "qq",
                require_cookies=require_cookies,
                allow_degraded=allow_degraded,
            )

    async def refresh_qq_cookies(
        self,
        login_account_id: int | None = None,
        *,
        allow_degraded: bool = False,
    ) -> bool:
        """通过Playwright访问QQ空间，自动刷新/续期cookies。
        
        原理：用现有cookies加载QQ空间页面，QQ服务器会自动续期session。
        刷新后提取新cookies保存到数据库。
        返回True=续期成功，False=cookies已失效需重新登录。
        """
        account = await self._load_login_account(
            login_account_id,
            require_cookies=True,
            allow_degraded=allow_degraded,
        )
        if not account or not account.cookies:
            logger.info("QQ Cookie续期：无登录账号")
            return False

        try:
            old_cookies = json.loads(account.cookies)
        except Exception:
            logger.warning("QQ Cookie续期：Cookie解析失败")
            return False

        last_login = account.last_login

        # 检查上次登录时间，如果不到1小时则不需要刷新
        if last_login:
            elapsed = (datetime.now() - last_login).total_seconds()
            if elapsed < 3600:
                logger.debug(f"QQ Cookie续期：距上次刷新仅{int(elapsed)}秒，跳过")
                return True

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("QQ Cookie续期：Playwright未安装")
            return False

        logger.info("QQ Cookie续期开始...")
        p = None
        browser = None
        try:
            p = await async_playwright().start()
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            await context.add_cookies(old_cookies)
            page = await context.new_page()

            # 导航到QQ空间首页（会自动刷新session cookies）
            try:
                await page.goto("https://user.qzone.qq.com/", wait_until="domcontentloaded", timeout=20000)
            except Exception:
                pass
            await asyncio.sleep(3)

            # 检查是否已获得p_skey，没有则尝试更多QQ空间URL
            new_cookies = await context.cookies()
            has_p_skey = any(c.get("name") == "p_skey" and c.get("value") for c in new_cookies)
            if not has_p_skey:
                # 从cookie中提取uin用于构造SSO URL
                uin_val = ""
                pt_key_val = ""
                for c in new_cookies:
                    if c.get("name") == "uin":
                        uin_val = c.get("value", "").lstrip("o0")
                    if c.get("name") == "pt_key":
                        pt_key_val = c.get("value", "")
                    if c.get("name") == "skey":
                        skey_val = c.get("value", "")

                # 尝试通过QQ空间SSO登录URL获取p_skey
                sso_urls = [
                    "https://qzone.qq.com",
                    "https://h5.qzone.qq.com/mqzone/index",
                ]
                if uin_val:
                    sso_urls.insert(0, f"https://user.qzone.qq.com/{uin_val}")
                for sso_url in sso_urls:
                    try:
                        await page.goto(sso_url, wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(3)
                        new_cookies = await context.cookies()
                        has_p_skey = any(c.get("name") == "p_skey" and c.get("value") for c in new_cookies)
                        if has_p_skey:
                            logger.info(f"QQ Cookie续期: 通过 {sso_url} 获得p_skey")
                            break
                    except Exception as e:
                        logger.debug(f"QQ Cookie续期: 导航到 {sso_url} 失败: {e}")

            # 最终提取cookies
            new_cookies = await context.cookies()
            if not new_cookies:
                logger.warning("QQ Cookie续期：未获取到新cookies")
                return False

            # 检查关键cookie是否存在（必须有非空value）
            has_p_skey_final = any(c.get("name") == "p_skey" and c.get("value") for c in new_cookies)
            has_skey = any(c.get("name") == "skey" and c.get("value") for c in new_cookies)
            if not has_p_skey_final and not has_skey:
                logger.warning("QQ Cookie续期：缺少关键认证cookie(p_skey/skey)，可能已过期")
                # 不在此处标记expired或发送通知，由调用方(_cookie_refresh_wrapper)在所有重试耗尽后统一处理
                return False

            # 验证新cookies是否有效：尝试访问一个需要登录的API
            try:
                uin = ""
                p_skey = ""
                for c in new_cookies:
                    if c["name"] == "uin":
                        uin = c["value"].lstrip("o0")
                    if c["name"] == "p_skey":
                        p_skey = c["value"]
                    if c["name"] == "skey" and not p_skey:
                        p_skey = c["value"]

                if uin and p_skey:
                    g_tk = compute_g_tk(p_skey)
                    verify_url = f"https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6?uin={uin}&ftype=0&sort=0&pos=0&num=1&g_tk={g_tk}&format=json"
                    resp = await page.evaluate(f"""async () => {{
                        try {{
                            const r = await fetch("{verify_url}", {{credentials: 'include'}});
                            return {{status: r.status}};
                        }} catch(e) {{
                            return {{error: e.message}};
                        }}
                    }}""")
                    if isinstance(resp, dict) and resp.get("status") == 200:
                        logger.info("QQ Cookie续期验证成功")
                    else:
                        logger.warning(f"QQ Cookie续期验证响应: {resp}")
            except Exception as e:
                logger.debug(f"QQ Cookie续期验证异常（非致命）: {e}")

            # 保存刷新后的cookies
            await self._save_login_cookies(new_cookies, login_account_id=account.id)
            logger.info(f"QQ Cookie续期成功（{len(new_cookies)}个cookie）")
            return True

        except Exception as e:
            logger.error(f"QQ Cookie续期失败: {type(e).__name__}: {e}")
            return False
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            if p:
                try:
                    await p.stop()
                except Exception:
                    pass

    # ===================== QQ空间 HTTP API 爬取 =====================

    async def start_crawl(
        self,
        qq_numbers: list[str],
        mode: str = "incremental",
        login_account_id: int | None = None,
        allow_degraded_login: bool = False,
    ) -> str:
        if self._status.get("running"):
            raise RuntimeError("已有QQ空间抓取任务正在运行，请等待完成后再启动")

        task_id = uuid.uuid4().hex[:8]
        self._status = {
            "running": True,
            "task_id": task_id,
            "progress": "启动中...",
            "error": None,
            "details": [],
            "results": [],
        }
        self._crawl_mode = mode  # "incremental" or "overwrite"
        self._log(f"QQ抓取任务启动: task_id={task_id}, QQ号={qq_numbers}, 模式={mode}")
        self._active_task = asyncio.create_task(
            self._run_crawl_locked(
                qq_numbers,
                task_id,
                login_account_id=login_account_id,
                allow_degraded_login=allow_degraded_login,
            )
        )
        return task_id

    async def wait_for_task(self, task_id: str | None = None) -> dict:
        task = self._active_task
        if task and (task_id is None or self._status.get("task_id") == task_id):
            await task
        return self._status

    async def run_crawl(
        self,
        qq_numbers: list[str],
        mode: str = "incremental",
        login_account_id: int | None = None,
        allow_degraded_login: bool = False,
    ) -> dict:
        task_id = await self.start_crawl(
            qq_numbers,
            mode=mode,
            login_account_id=login_account_id,
            allow_degraded_login=allow_degraded_login,
        )
        return await self.wait_for_task(task_id)

    async def _run_crawl_locked(
        self,
        qq_numbers: list[str],
        task_id: str,
        *,
        login_account_id: int | None = None,
        allow_degraded_login: bool = False,
    ):
        async with self._crawl_lock:
            try:
                await self._crawl_task(
                    qq_numbers,
                    task_id,
                    login_account_id=login_account_id,
                    allow_degraded_login=allow_degraded_login,
                )
            finally:
                if self._status.get("task_id") == task_id:
                    self._active_task = None

    async def _crawl_task(
        self,
        qq_numbers: list[str],
        task_id: str,
        *,
        login_account_id: int | None = None,
        allow_degraded_login: bool = False,
    ):
        try:
            self._log("尝试QQ空间HTTP API抓取")
            api_ok = await self._crawl_via_api(
                qq_numbers,
                login_account_id=login_account_id,
                allow_degraded_login=allow_degraded_login,
            )
            if not api_ok:
                self._log("HTTP API抓取失败，降级到Playwright浏览器抓取", "warning")
                await self._crawl_via_playwright(
                    qq_numbers,
                    login_account_id=login_account_id,
                    allow_degraded_login=allow_degraded_login,
                )
            if not self._status.get("error"):
                self._status["progress"] = "完成"
                self._log("抓取任务完成")
        except Exception as e:
            logger.error(f"QQ抓取任务失败: {e}", exc_info=True)
            self._status["error"] = str(e)
            self._log(f"抓取任务异常: {e}", "error")
        finally:
            self._status["running"] = False

    async def _get_login_cookies_and_token(
        self,
        login_account_id: int | None = None,
        *,
        allow_degraded: bool = False,
    ) -> tuple[dict, int, str] | None:
        """从数据库获取QQ登录Cookie和g_tk token。返回 (cookies_dict, g_tk, uin)"""
        account = await self._load_login_account(
            login_account_id,
            require_cookies=True,
            allow_degraded=allow_degraded,
        )
        if not account or not account.cookies:
            return None

        cookies_list = json.loads(account.cookies)
        cookies_dict = {}
        p_skey = ""
        skey = ""
        pt_key = ""
        uin = ""

        for c in cookies_list:
            name = c.get("name", "")
            value = c.get("value", "")
            cookies_dict[name] = value
            if name == "p_skey" and value:
                p_skey = value
            if name == "skey" and value:
                skey = value
            if name == "pt_key" and value:
                pt_key = value
            if name == "uin":
                uin = value.lstrip("o0")

        token_key = p_skey or skey or pt_key
        if not token_key:
            self._log("QQ Cookie中无p_skey/skey/pt_key，无法计算g_tk", "warning")
            return None

        g_tk = compute_g_tk(token_key)
        used_key = "p_skey" if p_skey else ("skey" if skey else "pt_key")
        self._log(f"已加载QQ Cookie（uin={uin}, g_tk={g_tk}, 使用{used_key}）")
        return cookies_dict, g_tk, uin

    def _build_post_id(self, qq: str, msg: dict) -> str:
        tid = str(msg.get("tid") or "").strip()
        if tid:
            return f"qz_{qq}_{tid}"

        created_time = str(msg.get("created_time") or "").strip()
        raw_fingerprint = json.dumps(
            {
                "created_time": created_time,
                "content": msg.get("content", ""),
                "source_name": msg.get("source_name", ""),
                "pic": msg.get("pic", []),
                "video": msg.get("video", []),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha1(raw_fingerprint.encode("utf-8")).hexdigest()[:12]
        return f"qz_{qq}_{created_time or 'unknown'}_{digest}"

    def _build_comment_id(self, cmt: dict) -> str:
        cmt_id = str(cmt.get("tid") or "").strip()
        if cmt_id:
            return cmt_id

        raw_fingerprint = json.dumps(
            {
                "uin": cmt.get("uin", ""),
                "name": cmt.get("name", ""),
                "content": cmt.get("content", ""),
                "create_time": cmt.get("create_time", ""),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha1(raw_fingerprint.encode("utf-8")).hexdigest()[:16]

    def _is_local_static_file_missing(self, local_path: str | None) -> bool:
        if not local_path:
            return True
        if not local_path.startswith("/static/"):
            return False
        file_path = os.path.join(settings.static_dir, local_path[len("/static/"):])
        return not os.path.isfile(file_path)

    def _normalize_media_url_candidates(self, candidates) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for candidate in candidates or []:
            url = candidate.strip() if isinstance(candidate, str) else str(candidate or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            normalized.append(url)
        return normalized

    def _build_api_image_sources(self, pics: list[dict] | None) -> list[dict]:
        image_sources: list[dict] = []
        for pic in pics or []:
            candidate_urls = self._normalize_media_url_candidates([
                pic.get("url3", ""),
                pic.get("url2", ""),
                pic.get("url1", ""),
            ])
            if not candidate_urls:
                continue
            image_sources.append({
                "url": candidate_urls[0],
                "candidates": candidate_urls,
            })
        return image_sources

    async def _download_image_records(self, raw_images: list | None) -> list[dict]:
        from app.services.media_service import media_service

        image_records: list[dict] = []
        for raw_image in raw_images or []:
            primary_url = ""
            candidate_urls: list[str] = []

            if isinstance(raw_image, dict):
                primary_url = str(raw_image.get("url") or "").strip()
                candidate_urls = self._normalize_media_url_candidates(
                    raw_image.get("candidates")
                    or raw_image.get("urls")
                    or [primary_url]
                )
            elif isinstance(raw_image, (list, tuple, set)):
                candidate_urls = self._normalize_media_url_candidates(list(raw_image))
            else:
                primary_url = str(raw_image or "").strip()
                candidate_urls = self._normalize_media_url_candidates([primary_url])

            if not primary_url and candidate_urls:
                primary_url = candidate_urls[0]
            if not primary_url and not candidate_urls:
                continue

            local_path = await media_service.download_image_multi_url(candidate_urls, "qq") if candidate_urls else None
            image_records.append({
                "url": primary_url,
                "local_path": local_path,
            })

        return image_records

    def _build_image_source_for_record(self, image_item: dict | None, pic_sources: list[dict]) -> dict | None:
        original_url = str((image_item or {}).get("url") or "").strip()
        if not original_url:
            return None
        for source in pic_sources:
            if original_url == source.get("url"):
                return source
            if original_url in (source.get("candidates") or []):
                return source
        return {"url": original_url, "candidates": [original_url]}

    def _count_missing_image_records(self, image_records: list[dict] | None) -> int:
        missing = 0
        for image_item in image_records or []:
            if not isinstance(image_item, dict):
                missing += 1
                continue
            if self._is_local_static_file_missing(image_item.get("local_path")):
                missing += 1
        return missing

    def _normalize_existing_image_records(self, current_images: list | None) -> list[dict]:
        normalized: list[dict] = []
        for image_item in current_images or []:
            if isinstance(image_item, dict):
                normalized.append(dict(image_item))
            else:
                normalized.append({
                    "url": str(image_item or "").strip(),
                    "local_path": None,
                })
        return normalized

    def _find_preservable_image_record(
        self,
        source: dict | None,
        current_records: list[dict],
        *,
        used_indexes: set[int],
        preferred_index: int | None = None,
    ) -> tuple[int | None, dict | None]:
        candidate_urls = set(
            self._normalize_media_url_candidates(
                (source or {}).get("candidates")
                or [(source or {}).get("url", "")]
            )
        )

        for idx, record in enumerate(current_records):
            if idx in used_indexes or self._is_local_static_file_missing(record.get("local_path")):
                continue
            existing_url = str(record.get("url") or "").strip()
            if existing_url and existing_url in candidate_urls:
                return idx, dict(record)

        if preferred_index is not None and 0 <= preferred_index < len(current_records):
            record = current_records[preferred_index]
            if preferred_index not in used_indexes and not self._is_local_static_file_missing(record.get("local_path")):
                return preferred_index, dict(record)

        return None, None

    async def _ensure_image_records_local(
        self,
        post_id: str,
        current_images: list | None,
        pic_sources: list[dict],
        *,
        overwrite: bool,
    ) -> tuple[list[dict], bool, int]:
        current_records = self._normalize_existing_image_records(current_images)

        if overwrite:
            if not pic_sources:
                preserved_records = [
                    dict(record)
                    for record in current_records
                    if not self._is_local_static_file_missing(record.get("local_path"))
                ]
                if preserved_records:
                    self._log(
                        f"覆盖图片: {post_id} 未拿到新图片资源，保留 {len(preserved_records)} 张现有本地图片",
                        "warning",
                    )
                    return preserved_records, preserved_records != current_records, 0
                return [], bool(current_records), 0

            downloaded_records = await self._download_image_records(pic_sources)
            overwritten_records: list[dict] = []
            used_indexes: set[int] = set()
            preserved_count = 0

            for idx, source in enumerate(pic_sources):
                downloaded_record = dict(downloaded_records[idx]) if idx < len(downloaded_records) else {
                    "url": str(source.get("url") or "").strip(),
                    "local_path": None,
                }

                match_idx, preserved_record = self._find_preservable_image_record(
                    source,
                    current_records,
                    used_indexes=used_indexes,
                    preferred_index=idx,
                )

                if not self._is_local_static_file_missing(downloaded_record.get("local_path")):
                    if match_idx is not None:
                        used_indexes.add(match_idx)
                elif preserved_record:
                    downloaded_record["local_path"] = preserved_record.get("local_path")
                    if not downloaded_record.get("url"):
                        downloaded_record["url"] = preserved_record.get("url", "")
                    used_indexes.add(match_idx)
                    preserved_count += 1

                overwritten_records.append(downloaded_record)

            for idx, record in enumerate(current_records):
                if idx in used_indexes or self._is_local_static_file_missing(record.get("local_path")):
                    continue
                overwritten_records.append(dict(record))
                preserved_count += 1

            missing_after = self._count_missing_image_records(overwritten_records)
            if pic_sources:
                done = len(overwritten_records) - missing_after
                if preserved_count:
                    self._log(
                        f"覆盖图片: {post_id} → {done}/{len(overwritten_records)} 张已本地化，保留 {preserved_count} 张现有本地图片",
                        "warning",
                    )
                else:
                    self._log(f"覆盖图片: {post_id} → {done}/{len(overwritten_records)} 张已本地化")
            return overwritten_records, overwritten_records != current_records, missing_after

        if not current_records:
            downloaded_records = await self._download_image_records(pic_sources)
            missing_after = self._count_missing_image_records(downloaded_records)
            if downloaded_records:
                done = len(downloaded_records) - missing_after
                self._log(f"补充图片: {post_id} → {done}/{len(downloaded_records)} 张已本地化")
            return downloaded_records, bool(downloaded_records), missing_after

        changed = False
        repaired = 0
        new_images: list[dict] = []
        existing_urls: set[str] = set()

        for image_item in current_records:
            if isinstance(image_item, dict):
                record = dict(image_item)
            else:
                record = {
                    "url": str(image_item or "").strip(),
                    "local_path": None,
                }
                changed = True
            if record.get("url"):
                existing_urls.add(record["url"])

            if not self._is_local_static_file_missing(record.get("local_path")):
                new_images.append(record)
                continue

            if record.get("local_path"):
                record["local_path"] = None
                changed = True

            source = self._build_image_source_for_record(record, pic_sources)
            downloaded = await self._download_image_records([source] if source else [])
            if downloaded and downloaded[0].get("local_path"):
                repaired_record = downloaded[0]
                record["url"] = repaired_record.get("url") or record.get("url", "")
                record["local_path"] = repaired_record["local_path"]
                changed = True
                repaired += 1
                self._log(f"修复图片: {post_id} → {record['local_path']}")
            new_images.append(record)

        for source in pic_sources:
            source_url = str(source.get("url") or "").strip()
            if not source_url or source_url in existing_urls:
                continue
            downloaded = await self._download_image_records([source])
            if downloaded:
                new_images.extend(downloaded)
                changed = True
                existing_urls.add(source_url)

        missing_after = self._count_missing_image_records(new_images)
        if repaired and missing_after:
            self._log(f"QQ说说 {post_id} 仍有 {missing_after} 张图片未能恢复到本地", "warning")
        return new_images, changed, missing_after

    async def _download_qq_avatar(self, author_uin: str) -> str | None:
        from app.services.media_service import media_service

        if not author_uin:
            return None
        avatar_cdn = f"https://q.qlogo.cn/headimg_dl?dst_uin={author_uin}&spec=640&img_type=jpg"
        return await media_service.download_image(avatar_cdn, "avatar")

    def _extract_video_url(self, video_list: list | None) -> str:
        for video_item in video_list or []:
            if not isinstance(video_item, dict):
                continue
            video_url = (
                video_item.get("url3", "")
                or video_item.get("url2", "")
                or video_item.get("url1", "")
                or video_item.get("video_url", "")
            )
            if video_url:
                return video_url
        return ""

    async def _download_qq_video(self, video_url: str) -> str | None:
        from app.services.media_service import media_service

        if not video_url:
            return None
        return await media_service.download_video(video_url, "qq")

    async def ensure_post_media_local(self, post, msg: dict | None = None, *, overwrite: bool = False) -> dict:
        from sqlalchemy.orm.attributes import flag_modified

        payload = msg if isinstance(msg, dict) else (post.raw_data if isinstance(getattr(post, "raw_data", None), dict) else {})
        post_id = getattr(post, "post_id", "") or str(getattr(post, "id", "") or "unknown")
        pic_sources = self._build_api_image_sources(payload.get("pic", []))
        current_images = getattr(post, "images", None) or []

        image_records, images_changed, missing_images = await self._ensure_image_records_local(
            post_id,
            current_images,
            pic_sources,
            overwrite=overwrite,
        )
        image_state_changed = image_records != self._normalize_existing_image_records(current_images)
        if image_state_changed:
            post.images = image_records
            try:
                flag_modified(post, "images")
            except Exception:
                pass

        status = {
            "images_changed": image_state_changed,
            "missing_images": missing_images,
            "avatar_repaired": False,
            "avatar_missing": False,
            "video_repaired": False,
            "video_missing": False,
            "changed": image_state_changed,
        }

        author_uin = str(payload.get("uin") or getattr(post, "author_qq", "") or getattr(post, "qq_number", "") or "")
        if author_uin and self._is_local_static_file_missing(getattr(post, "author_avatar", None)):
            current_avatar = getattr(post, "author_avatar", None)
            local_avatar = await self._download_qq_avatar(author_uin)
            if local_avatar:
                post.author_avatar = local_avatar
                status["avatar_repaired"] = True
                status["changed"] = True
            else:
                if current_avatar:
                    post.author_avatar = ""
                    status["changed"] = True
                status["avatar_missing"] = True

        video_url = self._extract_video_url(payload.get("video", [])) or str(getattr(post, "video_url", "") or "").strip()
        if video_url:
            post.video_url = video_url
            if overwrite or self._is_local_static_file_missing(getattr(post, "local_video_path", None)):
                current_local_video = getattr(post, "local_video_path", "")
                current_local_video_available = bool(current_local_video) and not self._is_local_static_file_missing(current_local_video)
                if current_local_video and not current_local_video_available:
                    self._log(f"QQ说说 {post_id} 本地视频缺失，尝试重新下载", "warning")
                local_video = await self._download_qq_video(video_url)
                if local_video:
                    post.local_video_path = local_video
                    status["video_repaired"] = True
                    status["changed"] = True
                    self._log(f"补充/修复视频: {post_id}")
                elif current_local_video_available:
                    self._log(f"QQ说说 {post_id} 覆盖视频下载失败，保留现有本地视频", "warning")
                else:
                    if current_local_video:
                        status["changed"] = True
                    post.local_video_path = ""
                    status["video_missing"] = True
        elif overwrite and (getattr(post, "video_url", "") or getattr(post, "local_video_path", "")):
            current_local_video = getattr(post, "local_video_path", "")
            if current_local_video and not self._is_local_static_file_missing(current_local_video):
                self._log(f"QQ说说 {post_id} 覆盖结果未返回视频资源，保留现有本地视频", "warning")
            else:
                post.video_url = ""
                post.local_video_path = ""
                status["changed"] = True

        return status

    def _post_has_missing_local_media(self, post) -> bool:
        if self._is_local_static_file_missing(getattr(post, "author_avatar", None)):
            return True
        if getattr(post, "video_url", "") and self._is_local_static_file_missing(getattr(post, "local_video_path", None)):
            return True
        return self._count_missing_image_records(getattr(post, "images", None) or []) > 0

    async def _backfill_missing_local_media(self, qq: str) -> dict[str, int]:
        from sqlalchemy import select
        from app.database import async_session
        from app.models.qq_post import QQPost

        scanned = 0
        repaired = 0
        unresolved = 0
        changed = False

        async with async_session() as db:
            result = await db.execute(select(QQPost).where(QQPost.qq_number == qq))
            posts = result.scalars().all()
            for post in posts:
                if not self._post_has_missing_local_media(post):
                    continue
                scanned += 1
                status = await self.ensure_post_media_local(post, overwrite=False)
                if status["images_changed"] or status["avatar_repaired"] or status["video_repaired"]:
                    repaired += 1
                if status.get("changed"):
                    changed = True
                if status["missing_images"] or status["avatar_missing"] or status["video_missing"]:
                    unresolved += 1
            if changed:
                await db.commit()

        if scanned:
            self._log(f"QQ={qq} 历史媒体回补: 扫描 {scanned} 条，修复 {repaired} 条，仍缺失 {unresolved} 条")
        return {"scanned": scanned, "repaired": repaired, "unresolved": unresolved}

    async def _save_qq_comments(self, db, post, comments: list[dict]) -> int:
        if not comments:
            return 0

        from app.models.qq_post import QQComment
        from sqlalchemy import select

        await db.flush()
        existing_result = await db.execute(
            select(QQComment).where(QQComment.post_id == post.id)
        )
        existing_cmts = {c.comment_id: c for c in existing_result.scalars().all() if c.comment_id}
        added = 0
        for cmt in comments:
            cmt_id = self._build_comment_id(cmt)
            if not cmt_id:
                continue

            comment_time = None
            if cmt.get("create_time"):
                try:
                    comment_time = datetime.fromtimestamp(int(cmt["create_time"]))
                except (ValueError, OSError, TypeError):
                    pass

            existing = existing_cmts.get(cmt_id)
            if existing:
                existing.author_qq = str(cmt.get("uin", "")) or existing.author_qq
                existing.author_nickname = cmt.get("name", "") or existing.author_nickname
                existing.content = cmt.get("content", "") or existing.content
                existing.comment_time = comment_time or existing.comment_time
                continue

            comment = QQComment(
                post_id=post.id,
                comment_id=cmt_id,
                author_qq=str(cmt.get("uin", "")),
                author_nickname=cmt.get("name", ""),
                content=cmt.get("content", ""),
                comment_time=comment_time,
            )
            db.add(comment)
            existing_cmts[cmt_id] = comment
            added += 1
        return added

    async def _fetch_qq_detail_comments(self, session: aiohttp.ClientSession, qq: str, tid: str, g_tk: int) -> list[dict]:
        """Best-effort supplement for posts whose embedded commentlist is truncated."""
        if not tid:
            return []

        detail_url = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msgdetail_v6"
        collected: list[dict] = []
        seen: set[str] = set()
        pos = 0
        for _ in range(20):
            params = {
                "uin": qq,
                "tid": tid,
                "ftype": 0,
                "sort": 0,
                "pos": pos,
                "num": 100,
                "replynum": 100,
                "g_tk": g_tk,
                "callback": "_preloadCallback",
                "code_version": 1,
                "format": "jsonp",
                "need_private_comment": 1,
            }
            try:
                async with session.get(detail_url, params=params) as resp:
                    text = await resp.text()
                match = re.search(r'_preloadCallback\((.*)\)', text, re.DOTALL)
                if not match:
                    break
                data = json.loads(match.group(1))
            except Exception as exc:
                self._log(f"QQ={qq} 说说 {tid} 评论补拉失败: {exc}", "warning")
                break

            if data.get("code", 0) not in (0, None):
                self._log(f"QQ={qq} 说说 {tid} 评论补拉返回 code={data.get('code')}", "warning")
                break

            comments = (
                data.get("commentlist")
                or data.get("comments")
                or (data.get("msg") or {}).get("commentlist")
                or []
            )
            new_count = 0
            for cmt in comments:
                cmt_id = self._build_comment_id(cmt)
                if cmt_id and cmt_id not in seen:
                    seen.add(cmt_id)
                    collected.append(cmt)
                    new_count += 1

            if len(comments) < 100 or new_count == 0:
                break
            pos += 100
            await asyncio.sleep(0.25)

        return collected

    async def _crawl_via_api(
        self,
        qq_numbers: list[str],
        *,
        login_account_id: int | None = None,
        allow_degraded_login: bool = False,
    ) -> bool:
        """通过QQ空间HTTP JSON API抓取说说。返回True表示成功（至少部分成功）"""
        creds = await self._get_login_cookies_and_token(
            login_account_id,
            allow_degraded=allow_degraded_login,
        )
        if not creds:
            self._log("⚠ 没有找到已登录的QQ账号Cookie，HTTP API不可用", "warning")
            return False

        cookies_dict, g_tk, login_uin = creds

        from app.database import async_session
        from app.models.qq_post import QQPost, QQComment
        from app.services.media_service import media_service
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError

        url = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://user.qzone.qq.com/",
        }

        # 禁用SSL验证（QQ空间证书链不完整会导致SSLCertVerificationError）
        import ssl as _ssl
        ssl_ctx = _ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = _ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)

        async with aiohttp.ClientSession(cookies=cookies_dict, headers=headers, connector=connector) as session:
            any_success = False
            had_terminal_error = False
            for qq in qq_numbers:
                self._status["progress"] = f"正在抓取 {qq}..."
                self._log(f"开始抓取 QQ={qq} 说说")
                total_fetched = 0
                saved_count = 0
                updated_count = 0
                target_error = None
                stop_reason = "unknown"

                try:
                    pos = 0
                    page_num = 0
                    empty_pages = 0  # 连续空页计数
                    while True:
                        params = {
                            "uin": qq,
                            "ftype": 0,
                            "sort": 0,
                            "pos": pos,
                            "num": 40,
                            "replynum": 100,
                            "g_tk": g_tk,
                            "callback": "_preloadCallback",
                            "code_version": 1,
                            "format": "jsonp",
                            "need_private_comment": 1,
                        }

                        async with session.get(url, params=params) as resp:
                            text = await resp.text()

                        # 解析JSONP响应: _preloadCallback({...})
                        match = re.search(r'_preloadCallback\((.*)\)', text, re.DOTALL)
                        if not match:
                            # 详细记录失败原因，协助排查
                            resp_preview = text[:300].replace('\n', ' ').strip() if text else '(空响应)'
                            self._log(f"QQ={qq} 页{page_num}: JSONP解析失败，响应内容: {resp_preview}", "warning")
                            if page_num == 0:
                                if '<html' in text.lower() or 'login' in text.lower():
                                    self._status["error"] = "QQ空间Cookie已过期（返回登录页面），请重新扫码登录。"
                                else:
                                    self._status["error"] = f"QQ空间API响应异常：Cookie可能已过期，请重新扫码登录。"
                                await self._notify_cookie_expired("QQ空间", f"JSONP解析失败，响应前100字符: {resp_preview[:100]}")
                            target_error = "jsonp_parse_failed"
                            break

                        data = json.loads(match.group(1))
                        code = data.get("code", -1)
                        if code != 0:
                            self._log(f"QQ={qq} API返回错误 code={code}: {data.get('message', '')}", "warning")
                            if code == -3000:
                                self._status["error"] = "QQ空间Cookie已过期，请重新扫码登录。"
                                await self._notify_cookie_expired("QQ空间", f"API返回 code={code}")
                            target_error = f"api_code_{code}"
                            break

                        msglist = data.get("msglist")
                        if not msglist:
                            empty_pages += 1
                            self._log(f"QQ={qq} 页{page_num}: 无更多说说")
                            if empty_pages >= 2:
                                stop_reason = "empty_pages"
                                break
                            pos += 40
                            page_num += 1
                            continue

                        empty_pages = 0
                        total_fetched += len(msglist)
                        self._log(f"QQ={qq} 页{page_num}: 获取 {len(msglist)} 条说说 (累计{total_fetched})")

                        # 保存到数据库（支持增量更新）
                        async with async_session() as db:
                            for msg in msglist:
                                tid = msg.get("tid", "")
                                post_id = self._build_post_id(qq, msg)
                                legacy_post_id = ""
                                if not tid and msg.get("created_time"):
                                    legacy_post_id = f"qz_{qq}_{msg.get('created_time', '')}"

                                post_id_candidates = [post_id]
                                if legacy_post_id and legacy_post_id != post_id:
                                    post_id_candidates.append(legacy_post_id)
                                existing_result = await db.execute(
                                    select(QQPost).where(QQPost.post_id.in_(post_id_candidates))
                                )
                                candidate_posts = existing_result.scalars().all()
                                existing_post = next((p for p in candidate_posts if p.post_id == post_id), None)
                                legacy_post = next((p for p in candidate_posts if p.post_id == legacy_post_id), None)
                                if existing_post and legacy_post and existing_post.id != legacy_post.id:
                                    legacy_comments = await db.execute(
                                        select(QQComment).where(QQComment.post_id == legacy_post.id)
                                    )
                                    for comment in legacy_comments.scalars().all():
                                        comment.post_id = existing_post.id
                                    await db.delete(legacy_post)
                                    self._log(f"QQ说说 {legacy_post_id} 已合并到规范ID {post_id}", "warning")
                                elif legacy_post:
                                    existing_post = legacy_post
                                if existing_post and existing_post.post_id == legacy_post_id:
                                    existing_post.post_id = post_id

                                # 通用字段提取
                                new_content = msg.get("content", "")
                                new_like = msg.get("likenum", 0) if isinstance(msg.get("likenum"), int) else 0
                                new_comment = msg.get("commentnum", 0) if isinstance(msg.get("commentnum"), int) else 0
                                device_info = msg.get("source_name", "") or ""
                                location = ""
                                lbs = msg.get("lbs", {})
                                if lbs and isinstance(lbs, dict):
                                    location = lbs.get("idname", "") or lbs.get("name", "") or ""

                                if existing_post:
                                    from sqlalchemy.orm.attributes import flag_modified
                                    overwrite = getattr(self, '_crawl_mode', 'incremental') == 'overwrite'
                                    # --- 增量更新：检测变化 ---
                                    changes = {}
                                    if new_content and new_content != (existing_post.content or ""):
                                        changes["content"] = {"old": (existing_post.content or "")[:100], "new": new_content[:100]}
                                    if new_like != (existing_post.like_count or 0):
                                        changes["like_count"] = {"old": existing_post.like_count, "new": new_like}
                                    if new_comment != (existing_post.comment_count or 0):
                                        changes["comment_count"] = {"old": existing_post.comment_count, "new": new_comment}

                                    # 更新统计数据
                                    existing_post.like_count = new_like
                                    existing_post.comment_count = new_comment
                                    if device_info and not existing_post.device_info:
                                        existing_post.device_info = device_info
                                    if location and not existing_post.location:
                                        existing_post.location = location
                                    existing_post.raw_data = msg

                                    media_status = await self.ensure_post_media_local(
                                        existing_post,
                                        msg,
                                        overwrite=overwrite,
                                    )
                                    if media_status["missing_images"] or media_status["avatar_missing"] or media_status["video_missing"]:
                                        self._log(
                                            f"QQ说说 {post_id} 仍有媒体未能落本地: 图片缺失={media_status['missing_images']}, "
                                            f"头像缺失={media_status['avatar_missing']}, 视频缺失={media_status['video_missing']}",
                                            "warning",
                                        )
                                    if new_content:
                                        existing_post.content = new_content

                                    # 内容变化时记录编辑历史
                                    if "content" in changes:
                                        history = existing_post.edit_history or []
                                        history.append({
                                            "time": datetime.now().isoformat(),
                                            "changes": changes,
                                        })
                                        existing_post.edit_history = history
                                        existing_post.content = new_content
                                        self._log(f"QQ说说 {post_id} 内容已更新")

                                    # 增量更新评论
                                    commentlist = msg.get("commentlist") or []
                                    all_comments = list(commentlist)
                                    if tid and new_comment > len(commentlist):
                                        all_comments.extend(await self._fetch_qq_detail_comments(session, qq, tid, g_tk))
                                    if all_comments:
                                        added_comments = await self._save_qq_comments(db, existing_post, all_comments)
                                        if new_comment > len(all_comments):
                                            self._log(
                                                f"QQ说说 {post_id} 评论可能未完全补齐: 标记{new_comment}条，已获取{len(all_comments)}条",
                                                "warning",
                                            )
                                        elif added_comments:
                                            self._log(f"QQ说说 {post_id} 新增评论 {added_comments} 条")

                                    updated_count += 1
                                    continue

                                # --- 新说说：创建 ---
                                images = await self._download_image_records(
                                    self._build_api_image_sources(msg.get("pic", []))
                                )

                                # 下载作者头像
                                author_uin = str(msg.get("uin", qq))
                                local_avatar = await self._download_qq_avatar(author_uin)

                                post_time = None
                                if msg.get("created_time"):
                                    try:
                                        post_time = datetime.fromtimestamp(int(msg["created_time"]))
                                    except (ValueError, OSError):
                                        pass

                                forward_content = ""
                                if msg.get("rt_con") and msg["rt_con"].get("content"):
                                    forward_content = msg["rt_con"]["content"]

                                # 提取视频
                                video_url = self._extract_video_url(msg.get("video", []))
                                local_video_path = await self._download_qq_video(video_url) if video_url else ""

                                post = QQPost(
                                    qq_number=qq,
                                    post_id=post_id,
                                    author_qq=str(msg.get("uin", qq)),
                                    author_nickname=msg.get("name", ""),
                                    author_avatar=local_avatar or "",
                                    content=new_content,
                                    images=images,
                                    video_url=video_url,
                                    local_video_path=local_video_path,
                                    post_time=post_time or datetime.now(),
                                    like_count=new_like,
                                    comment_count=new_comment,
                                    forward_content=forward_content,
                                    device_info=device_info,
                                    location=location,
                                    raw_data=msg,
                                )
                                db.add(post)
                                saved_count += 1

                                # 保存评论；嵌入评论不足时尝试补拉详情页评论分页
                                commentlist = msg.get("commentlist", []) or []
                                all_comments = list(commentlist)
                                if tid and new_comment > len(commentlist):
                                    all_comments.extend(await self._fetch_qq_detail_comments(session, qq, tid, g_tk))
                                if all_comments:
                                    await self._save_qq_comments(db, post, all_comments)
                                    if new_comment > len(all_comments):
                                        self._log(
                                            f"QQ说说 {post_id} 评论可能未完全补齐: 标记{new_comment}条，已获取{len(all_comments)}条",
                                            "warning",
                                        )

                            try:
                                await db.commit()
                            except IntegrityError as exc:
                                await db.rollback()
                                self._log(f"QQ={qq} 页{page_num}: 数据唯一性冲突，已跳过本页异常记录: {exc}", "warning")
                                self._status["error"] = "QQ空间落库发生唯一性冲突，本轮该账号数据未完整保存。"
                                target_error = "db_integrity_error"
                                had_terminal_error = True
                                break

                        pos += 40
                        page_num += 1

                        # 如果返回数量少于请求数量，说明到底了
                        if len(msglist) < 40:
                            self._log(f"QQ={qq} 页{page_num}: 返回{len(msglist)}条 < 40，到底了")
                            stop_reason = "bottom"
                            break

                        # 安全上限防止无限循环
                        if page_num >= self._max_api_pages:
                            self._log(f"QQ={qq} 达到{self._max_api_pages}页上限", "warning")
                            stop_reason = "page_limit_reached"
                            break

                        await asyncio.sleep(0.5)  # 避免请求过快

                    backfill_result = {"scanned": 0, "repaired": 0, "unresolved": 0}
                    if not target_error:
                        backfill_result = await self._backfill_missing_local_media(qq)

                    if target_error:
                        self._log(f"QQ={qq} 抓取未完成: {target_error}", "warning")
                        self._status["progress"] = f"{qq}: 抓取未完成 ({target_error})"
                        self._status.setdefault("results", []).append({
                            "account_id": qq,
                            "status": "failed",
                            "reason": target_error,
                            "fetched": total_fetched,
                            "saved": saved_count,
                            "updated": updated_count,
                            "media_backfill": backfill_result,
                        })
                        continue

                    self._log(f"QQ={qq} 抓取完成: 总计 {total_fetched} 条，新增 {saved_count} 条，更新 {updated_count} 条，停止原因={stop_reason}")
                    self._status["progress"] = f"{qq}: 获取 {total_fetched} 条，新增 {saved_count}，更新 {updated_count}"
                    self._status.setdefault("results", []).append({
                        "account_id": qq,
                        "status": "empty" if total_fetched == 0 else "success",
                        "fetched": total_fetched,
                        "saved": saved_count,
                        "updated": updated_count,
                        "stop_reason": stop_reason,
                        "page_limit": self._max_api_pages if stop_reason == "page_limit_reached" else None,
                        "media_backfill": backfill_result,
                    })
                    any_success = True

                    # ---- 同步头像/昵称到 Account 表 ----
                    try:
                        from app.models.account import Account
                        avatar_cdn = f"https://q.qlogo.cn/headimg_dl?dst_uin={qq}&spec=640&img_type=jpg"
                        local_avatar = await media_service.download_image(avatar_cdn, "avatar")
                        async with async_session() as db2:
                            acct_result = await db2.execute(
                                select(Account).where(
                                    Account.platform == "qq",
                                    Account.account_id == qq,
                                )
                            )
                            acct = acct_result.scalar_one_or_none()
                            if acct:
                                if local_avatar:
                                    acct.avatar_url = local_avatar
                                # 从最近一条说说获取昵称
                                latest_post = await db2.execute(
                                    select(QQPost).where(QQPost.qq_number == qq)
                                    .order_by(QQPost.post_time.desc()).limit(1)
                                )
                                latest = latest_post.scalar_one_or_none()
                                if latest and latest.author_nickname and latest.author_qq == qq:
                                    acct.nickname = latest.author_nickname
                                await db2.commit()
                                self._log(f"QQ={qq} 已同步头像/昵称到账号管理")
                    except Exception as e_sync:
                        self._log(f"QQ={qq} 同步账号信息失败: {e_sync}", "warning")

                except Exception as e:
                    self._log(f"抓取 {qq} 失败: {e}", "error")
                    self._status["progress"] = f"抓取 {qq} 失败: {str(e)}"
                    self._status.setdefault("results", []).append({
                        "account_id": qq,
                        "status": "failed",
                        "reason": str(e),
                    })

            return any_success or had_terminal_error

    # ===================== Playwright DOM 解析（备用方案）=====================

    async def _crawl_via_playwright(
        self,
        qq_numbers: list[str],
        *,
        login_account_id: int | None = None,
        allow_degraded_login: bool = False,
    ):
        """通过Playwright浏览器自动化抓取QQ空间（备用方案）"""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self._log("Playwright未安装", "error")
            self._status["error"] = "HTTP API和Playwright均不可用"
            return

        from app.models.qq_post import QQPost
        from app.services.media_service import media_service

        self._log("正在启动Playwright浏览器（备用模式）...")
        try:
            pw = await async_playwright().start()
        except Exception as e:
            self._log(f"Playwright启动失败: {e}", "error")
            self._status["error"] = f"Playwright启动失败: {e}"
            return

        try:
            browser = await pw.chromium.launch(headless=True)

            account = await self._load_login_account(
                login_account_id,
                require_cookies=False,
                allow_degraded=allow_degraded_login,
            )
            cookies = None
            if account and account.cookies:
                cookies = json.loads(account.cookies)
                self._log(f"已加载QQ Cookie（账号: {account.account_id}）")
            else:
                self._log("⚠ 无QQ登录Cookie，Playwright模式可能无法访问空间", "warning")
                self._status["error"] = "未登录QQ：请先扫码登录QQ空间。"

            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            if cookies:
                await context.add_cookies(cookies)

            page = await context.new_page()

            for qq in qq_numbers:
                self._status["progress"] = f"Playwright抓取 {qq}..."
                self._log(f"[Playwright] 开始抓取 QQ={qq}")
                try:
                    url = f"https://user.qzone.qq.com/{qq}/311"
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    except Exception:
                        self._log(f"[Playwright] 页面加载超时，尝试继续", "warning")
                    await asyncio.sleep(4)

                    page_url = page.url
                    if "login" in page_url.lower() or "xui.ptlogin2" in page_url.lower():
                        self._log(f"⚠ 跳转到登录页: {page_url}，Cookie无效", "warning")
                        continue

                    # 尝试切换到iframe
                    frames = page.frames
                    target_frame = page
                    for frame in frames:
                        if "app_canvas_frame" in (frame.name or ""):
                            target_frame = frame
                            self._log("[Playwright] 已进入 app_canvas_frame")
                            break

                    # DOM解析说说 — 使用多种选择器策略覆盖新旧QQ空间版本
                    # 使用new RegExp()代替正则字面量，避免Playwright evaluate中/分隔符歧义
                    posts_data = await target_frame.evaluate("""
                        () => {
                            const posts = [];
                            // 预编译正则（使用RegExp构造器避免字面量解析问题）
                            const RE_LIKE = new RegExp('赞\\\\s*[\\\\(（]\\\\s*(\\\\d+)');
                            const RE_COMMENT = new RegExp('评论\\\\s*[\\\\(（]\\\\s*(\\\\d+)');
                            const RE_DEVICE = new RegExp('来自\\\\s*(.+?)(?:\\\\s*$|\\\\s*赞|\\\\s*评论)');
                            const RE_SPLIT = new RegExp('(?=赞\\\\s*[\\\\(（]\\\\s*\\\\d+)');
                            const RE_TIME = new RegExp('((?:\\\\d+年)?\\\\d+月\\\\d+日[\\\\s\\\\S]{0,20}|昨天[\\\\s\\\\S]{0,20}|前天[\\\\s\\\\S]{0,20}|\\\\d+天前[\\\\s\\\\S]{0,10}|\\\\d+小时前|\\\\d+分钟前|刚刚)');
                            const RE_DEVICE2 = new RegExp('来自\\\\s*(.+?)(?:\\\\s*$|\\\\s*赞)');
                            const RE_CLEAN1 = new RegExp('赞[\\\\s\\\\S]*$');
                            const RE_CLEAN2 = new RegExp('评论[\\\\s\\\\S]*$');
                            const RE_NAV = new RegExp('^(TA的说说|返回说说主页|说说)\\\\s*');

                            // 策略1: 新版QQ空间选择器
                            const selectors = [
                                '.f-single', '.f-item', '.feed',
                                '.msg-list li', '.remark-list li',
                                '[class*="feed-item"]', '[class*="shuoshuo"]',
                                '.mod-export', '.blog-cell', '.talk_item',
                            ];
                            let items = [];
                            for (const sel of selectors) {
                                const found = document.querySelectorAll(sel);
                                if (found.length > 0) { items = found; break; }
                            }

                            // 策略2: 尝试通过各种内容容器选择
                            if (items.length === 0) {
                                items = document.querySelectorAll('[id*="feed_"], [id*="msg_"], [class*="content"][class*="wrap"]');
                            }

                            for (const item of items) {
                                try {
                                    const content = (
                                        item.querySelector('.f-info, .txt-box-title, .f-ct, .content, .msg-content, [class*="content"]')?.innerText
                                        || item.querySelector('.text-content, .talk-content, [class*="talk"]')?.innerText
                                        || ''
                                    ).trim();
                                    const timeEl = item.querySelector('.info-detail, .ui-mr10, .f-info-time, .time, [class*="time"], .create-time, [class*="date"]');
                                    const timeText = (timeEl?.innerText || timeEl?.textContent || '').trim();
                                    const nickname = (
                                        item.querySelector('.f-nick, .user-name, .f-name, .nickname, [class*="nick"], [class*="author"]')?.innerText || ''
                                    ).trim();
                                    const avatar = item.querySelector('.f-head img, .user-avatar img, img[class*="avatar"], img[class*="head"]')?.src || '';
                                    const extractBackgroundUrl = value => {
                                        const match = (value || '').match(/url\\(["']?(.*?)["']?\\)/i);
                                        return match ? match[1] : '';
                                    };
                                    const collectImageCandidates = img => {
                                        const values = [
                                            img.currentSrc || '',
                                            img.src || '',
                                            img.getAttribute('data-src') || '',
                                            img.getAttribute('data-origin') || '',
                                            img.getAttribute('data-original') || '',
                                            img.getAttribute('data-loadsrc') || '',
                                            img.getAttribute('data-pic') || '',
                                            extractBackgroundUrl(img.getAttribute('style') || ''),
                                            extractBackgroundUrl(window.getComputedStyle(img).backgroundImage || ''),
                                            extractBackgroundUrl(window.getComputedStyle(img.parentElement || img).backgroundImage || ''),
                                        ];
                                        const srcset = img.getAttribute('srcset') || '';
                                        if (srcset) {
                                            srcset.split(',').forEach(part => {
                                                const src = (part.trim().split(/\\s+/)[0] || '').trim();
                                                if (src) values.push(src);
                                            });
                                        }

                                        const result = [];
                                        const seen = new Set();
                                        values.forEach(value => {
                                            const src = (value || '').trim();
                                            if (!src || src.startsWith('data:')) return;
                                            const lower = src.toLowerCase();
                                            if (lower.includes('avatar') || lower.includes('head') || lower.includes('icon') || lower.includes('emoji')) {
                                                return;
                                            }
                                            if (seen.has(src)) return;
                                            seen.add(src);
                                            result.push(src);
                                        });
                                        return result;
                                    };
                                    const images = [];
                                    item.querySelectorAll('img').forEach(img => {
                                        const candidates = collectImageCandidates(img);
                                        if (candidates.length > 0) {
                                            images.push({
                                                url: candidates[0],
                                                candidates,
                                            });
                                        }
                                    });
                                    // 提取点赞和评论数
                                    let likeCount = 0;
                                    let commentCount = 0;
                                    const statsText = item.innerText || '';
                                    const likeM = statsText.match(RE_LIKE);
                                    if (likeM) likeCount = parseInt(likeM[1]) || 0;
                                    const commentM = statsText.match(RE_COMMENT);
                                    if (commentM) commentCount = parseInt(commentM[1]) || 0;
                                    if (!likeCount) {
                                        const likeEl = item.querySelector('.praise-num, [class*="like"] .num, [class*="praise"]');
                                        likeCount = parseInt(likeEl?.innerText) || 0;
                                    }
                                    // 设备信息
                                    let deviceInfo = '';
                                    const deviceM = (timeText + ' ' + statsText).match(RE_DEVICE);
                                    if (deviceM) deviceInfo = deviceM[1].trim();
                                    if (content || images.length > 0) {
                                        posts.push({
                                            content, time: timeText, nickname, avatar, images,
                                            likeCount, commentCount, deviceInfo,
                                        });
                                    }
                                } catch(e) {}
                            }

                            // 策略3: 文本解析兜底
                            if (posts.length === 0) {
                                const bodyText = document.body?.innerText || '';
                                const blocks = bodyText.split(RE_SPLIT);
                                for (let i = 0; i < blocks.length && posts.length < 20; i++) {
                                    const block = blocks[i].trim();
                                    if (!block || block.length < 10) continue;
                                    const likeM = block.match(RE_LIKE);
                                    const timeM = block.match(RE_TIME);
                                    const deviceM = block.match(RE_DEVICE2);
                                    if (likeM || timeM) {
                                        let content = block;
                                        if (timeM) {
                                            const tIdx = block.indexOf(timeM[0]);
                                            if (tIdx > 0) content = block.substring(0, tIdx).trim();
                                        }
                                        content = content.replace(RE_CLEAN1, '').replace(RE_CLEAN2, '').trim();
                                        content = content.replace(RE_NAV, '').trim();
                                        if (content && content.length > 2) {
                                            posts.push({
                                                content,
                                                time: timeM ? timeM[0].trim() : '',
                                                nickname: '',
                                                avatar: '',
                                                images: [],
                                                likeCount: likeM ? parseInt(likeM[1]) || 0 : 0,
                                                commentCount: 0,
                                                deviceInfo: deviceM ? deviceM[1].trim() : '',
                                            });
                                        }
                                    }
                                }
                            }
                            return posts;
                        }
                    """)

                    self._log(f"[Playwright] DOM解析: {len(posts_data)} 条动态")

                    if len(posts_data) == 0:
                        self._log(f"[Playwright] 未找到动态，可能Cookie失效或权限不足", "warning")
                        try:
                            body_text = await target_frame.evaluate("() => document.body?.innerText?.slice(0, 500) || ''")
                            if body_text:
                                self._log(f"[Playwright] 页面文本: {body_text}", "debug")
                        except Exception:
                            pass
                        continue

                    # 保存
                    saved_count = 0
                    async with async_session() as db:
                        for i, pd in enumerate(posts_data):
                            # 使用内容哈希作为稳定ID（避免每次抓取都重复插入）
                            content_hash = hash((pd.get("content", "")[:100], pd.get("time", "")))
                            post_id = f"pw_{qq}_{abs(content_hash) % 10**12}"
                            existing = await db.execute(select(QQPost).where(QQPost.post_id == post_id))
                            if existing.scalar_one_or_none():
                                continue

                            images = await self._download_image_records(pd.get("images", []))
                            post = QQPost(
                                qq_number=qq,
                                post_id=post_id,
                                author_qq=qq,
                                author_nickname=pd.get("nickname", ""),
                                author_avatar="",
                                content=pd.get("content", ""),
                                images=images,
                                post_time=datetime.now(),
                                like_count=pd.get("likeCount", 0),
                                comment_count=pd.get("commentCount", 0),
                                raw_data=pd,
                            )
                            db.add(post)
                            saved_count += 1
                        await db.commit()

                    self._log(f"[Playwright] QQ={qq}: {len(posts_data)} 条，新增 {saved_count} 条")

                except Exception as e:
                    self._log(f"[Playwright] 抓取 {qq} 失败: {e}", "error")

            await browser.close()
        except Exception as e:
            self._log(f"[Playwright] 运行异常: {e}", "error")
            self._status["error"] = str(e)
        finally:
            try:
                await pw.stop()
            except Exception:
                pass


qq_crawler = QQCrawler()

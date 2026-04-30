import logging
import asyncio
import uuid
import json
import re
import os
import shutil
import base64
from typing import Optional
from datetime import datetime
from app.config import settings

logger = logging.getLogger(__name__)


class XHSCrawler:
    def __init__(self):
        self._status = {"running": False, "task_id": None, "progress": "", "error": None}
        self.login_status = "unknown"
        self.login_status_detail = ""
        self._browser_context = None
        self._poll_task = None
        self._login_debug = {
            "updated_at": None,
            "platform": "xhs",
            "url": "",
            "title": "",
            "status": "",
            "detail": "",
            "screenshot": "",
            "events": [],
        }

    def get_status(self) -> dict:
        return self._status

    def _debug_event(self, message: str):
        logger.info(f"[XHS-DEBUG] {message}")
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
        返回 dict 包含 screenshot(base64), url, title, status, detail, viewportWidth, viewportHeight；
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

        # 获取viewport尺寸（供前端坐标映射）
        try:
            vs = page.viewport_size
            if vs:
                frame["viewportWidth"] = vs["width"]
                frame["viewportHeight"] = vs["height"]
        except Exception:
            pass

        try:
            shot = await page.screenshot(type="jpeg", quality=55, timeout=5000)
            frame["screenshot"] = base64.b64encode(shot).decode()
        except Exception:
            try:
                shot = await page.screenshot(type="jpeg", quality=35, full_page=False, timeout=8000)
                frame["screenshot"] = base64.b64encode(shot).decode()
            except Exception:
                pass

        return frame

    @staticmethod
    def _parse_count(value) -> int:
        """将互动数据值解析为整数，支持 '1.2万' 等中文数字格式"""
        if value is None:
            return 0
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return 0
            try:
                # 处理中文单位: '1.2万' → 12000, '3亿' → 300000000
                if '万' in value:
                    return int(float(value.replace('万', '')) * 10000)
                if '亿' in value:
                    return int(float(value.replace('亿', '')) * 100000000)
                return int(float(value))
            except (ValueError, TypeError):
                return 0
        return 0

    async def navigate_to(self, url: str) -> dict:
        """在当前浏览器页面中导航到指定URL"""
        if not self._browser_context:
            return {"ok": False, "error": "浏览器未启动"}
        page = self._browser_context.get("page")
        if not page:
            return {"ok": False, "error": "无可用页面"}
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            return {"ok": True, "url": page.url}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def save_cookies_manual(self) -> dict:
        """手动保存当前浏览器会话的Cookie到数据库"""
        if not self._browser_context:
            return {"ok": False, "error": "浏览器未启动"}
        context = self._browser_context.get("context")
        page = self._browser_context.get("page")
        if not context:
            return {"ok": False, "error": "无浏览器上下文"}

        try:
            cookies = await context.cookies()
            if not cookies:
                return {"ok": False, "error": "当前无Cookie"}

            # 检查是否有登录标识cookie
            LOGIN_COOKIE_NAMES = {
                'web_session', 'access-token', 'access-token-v2',
                'customer-sso-sid', 'galaxy_creator_session_id',
            }
            cookie_names = {c["name"] for c in cookies}
            has_login = bool(cookie_names & LOGIN_COOKIE_NAMES)

            # 尝试获取用户信息
            xhs_user_id = ""
            nickname = ""
            login_red_id = ""
            my_avatar_url = ""

            if page:
                try:
                    me_result = await page.evaluate("""async () => {
                        try {
                            const resp = await fetch('https://edith.xiaohongshu.com/api/sns/web/v2/user/me', {
                                method: 'GET', credentials: 'include',
                                headers: { 'Accept': 'application/json', 'Origin': 'https://www.xiaohongshu.com', 'Referer': 'https://www.xiaohongshu.com/' }
                            });
                            if (!resp.ok) return {error: 'HTTP ' + resp.status};
                            const json = await resp.json();
                            if (json.code === 0 && json.data) {
                                return { ok: true, red_id: json.data.red_id || '', user_id: json.data.user_id || '',
                                         nickname: json.data.nickname || '', imageb: json.data.imageb || '', images: json.data.images || '' };
                            }
                            return {error: 'code=' + json.code};
                        } catch(e) { return {error: e.message}; }
                    }""")
                    if isinstance(me_result, dict) and me_result.get("ok"):
                        xhs_user_id = me_result.get("user_id", "")
                        nickname = me_result.get("nickname", "")
                        login_red_id = me_result.get("red_id", "")
                        my_avatar_url = me_result.get("imageb") or me_result.get("images") or ""
                except Exception as e:
                    logger.debug(f"手动保存Cookie时获取用户信息失败: {e}")

            # 下载头像
            login_avatar_local = None
            if my_avatar_url:
                try:
                    from app.services.media_service import media_service
                    login_avatar_local = await media_service.download_image(my_avatar_url, "avatar")
                except Exception:
                    pass

            # 保存到数据库
            from app.database import async_session
            from app.models.account import Account
            from app.services.account_risk_service import mark_account_verified
            from sqlalchemy import select

            async with async_session() as db:
                # 查找已有的同UID账号
                existing_account = None
                if xhs_user_id:
                    result = await db.execute(
                        select(Account).where(
                            Account.platform == "xhs", Account.is_target == 0,
                            Account.platform_uid == xhs_user_id,
                        )
                    )
                    existing_account = result.scalars().first()

                if existing_account:
                    existing_account.cookies = json.dumps(cookies)
                    existing_account.last_login = datetime.now()
                    if login_red_id:
                        existing_account.account_id = login_red_id
                    if nickname:
                        existing_account.nickname = nickname
                    if login_avatar_local:
                        existing_account.avatar_url = login_avatar_local
                    mark_account_verified(existing_account)
                    account = existing_account
                    logger.info(f"手动保存Cookie: 更新已有账号 uid={xhs_user_id}")
                else:
                    account = Account(
                        platform="xhs",
                        account_id=login_red_id or xhs_user_id or "xhs_login",
                        platform_uid=xhs_user_id,
                        nickname=nickname,
                        avatar_url=login_avatar_local or "",
                        cookies=json.dumps(cookies),
                        is_target=0,
                        last_login=datetime.now(),
                    )
                    mark_account_verified(account)
                    db.add(account)
                    logger.info(f"手动保存Cookie: 新增账号 uid={xhs_user_id}")
                await db.commit()

            self.login_status = "logged_in"
            self.login_status_detail = "Cookie手动保存成功"

            return {
                "ok": True,
                "cookies_count": len(cookies),
                "has_login_cookie": has_login,
                "user_id": xhs_user_id,
                "nickname": nickname,
                "red_id": login_red_id,
            }
        except Exception as e:
            logger.error(f"手动保存Cookie失败: {e}", exc_info=True)
            return {"ok": False, "error": str(e)}

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
                        Account.platform == "xhs",
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
                "xhs",
                require_cookies=require_cookies,
                allow_degraded=allow_degraded,
            )

    async def _mark_login_account_expired(self, login_account_id: int | None, *, reason: str):
        if login_account_id is None:
            return

        from app.database import async_session
        from app.models.account import Account
        from app.services.account_risk_service import mark_account_expired

        async with async_session() as db:
            account = await db.get(Account, login_account_id)
            if not account:
                return

            mark_account_expired(account, reason=reason)
            await db.commit()

    async def start_crawl(
        self,
        user_ids: list[str],
        mode: str = "incremental",
        login_account_id: int | None = None,
        allow_degraded_login: bool = False,
    ) -> str:
        task_id = uuid.uuid4().hex[:8]
        self._status = {"running": True, "task_id": task_id, "progress": "启动中...", "error": None}
        self._crawl_mode = mode  # "incremental" or "overwrite"
        asyncio.create_task(
            self._crawl_task(
                user_ids,
                task_id,
                login_account_id=login_account_id,
                allow_degraded_login=allow_degraded_login,
            )
        )
        return task_id

    async def _crawl_task(
        self,
        user_ids: list[str],
        task_id: str,
        *,
        login_account_id: int | None = None,
        allow_degraded_login: bool = False,
    ):
        try:
            login_account = await self._load_login_account(
                login_account_id,
                require_cookies=True,
                allow_degraded=allow_degraded_login,
            )
            if not login_account or not login_account.cookies:
                self._status["error"] = "请先在设置页面扫码登录小红书账号，否则无法爬取"
                self._status["running"] = False
                logger.warning("XHS无已登录账号，请先扫码登录小红书")
                return

            success = await self._crawl_via_api(
                user_ids,
                login_account_id=login_account.id,
                allow_degraded_login=allow_degraded_login,
            )
            if not success:
                logger.info("API抓取失败，降级到Playwright")
                await self._crawl_via_playwright(
                    user_ids,
                    login_account_id=login_account.id,
                    allow_degraded_login=allow_degraded_login,
                )
            self._status["progress"] = "完成"
        except Exception as e:
            logger.error(f"小红书抓取任务失败: {e}")
            self._status["error"] = str(e)
        finally:
            self._status["running"] = False

    async def _crawl_via_api(
        self,
        user_ids: list[str],
        *,
        login_account_id: int | None = None,
        allow_degraded_login: bool = False,
    ) -> bool:
        """通过Playwright浏览器response拦截抓取小红书笔记（利用XHS自身JS签名，彻底解决406）

        核心原理：导航到用户主页，XHS自身JS自动发出签名请求，我们拦截API响应获取数据。
        """
        account = await self._load_login_account(
            login_account_id,
            require_cookies=True,
            allow_degraded=allow_degraded_login,
        )
        if not account or not account.cookies:
            logger.warning("XHS无已登录账号Cookie，API模式不可用")
            return False
        cookies_str = account.cookies

        try:
            cookies = json.loads(cookies_str)
        except Exception:
            logger.warning("XHS Cookie解析失败")
            return False

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return False

        p = None
        browser = None
        context = None
        try:
            p = await async_playwright().start()
            # 使用持久化浏览器上下文，保持设备指纹一致
            browser_data_dir = os.path.join(
                os.path.dirname(os.path.abspath(settings.static_dir)),
                "browser_data", "xhs_crawl"
            )
            os.makedirs(browser_data_dir, exist_ok=True)
            context = await p.chromium.launch_persistent_context(
                browser_data_dir,
                headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled', '--disable-infobars'],
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                extra_http_headers={
                    'Sec-CH-UA': '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
                    'Sec-CH-UA-Mobile': '?0',
                    'Sec-CH-UA-Platform': '"Windows"',
                },
            )
            # 注入反检测脚本
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => {
                        const mk = (n, f, d, m) => { const p = { name: n, filename: f, description: d, length: 1 }; p[0] = { type: m, suffixes: 'pdf', description: d, enabledPlugin: p }; return p; };
                        const pl = [ mk('Chrome PDF Plugin', 'internal-pdf-viewer', 'Portable Document Format', 'application/x-google-chrome-pdf'), mk('Chrome PDF Viewer', 'mhjfbmdgcfjbbpaeojofohoefgiehjai', '', 'application/pdf'), mk('Native Client', 'internal-nacl-plugin', '', 'application/x-nacl') ];
                        pl.item = i => pl[i] || null; pl.namedItem = n => pl.find(p => p.name === n) || null; pl.refresh = () => {}; return pl;
                    },
                });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
                window.chrome = { app: { isInstalled: false }, runtime: { connect: function() { return { onDisconnect: { addListener: function() {} } }; }, id: undefined }, loadTimes: function() { const t = Date.now()/1000; return { requestTime: t-2, startLoadTime: t-1.8, commitLoadTime: t-1.5, finishDocumentLoadTime: t-0.5, finishLoadTime: t-0.2, firstPaintTime: t-1.2, navigationType: 'Other', wasFetchedViaSpdy: true, wasNpnNegotiated: true, npnNegotiatedProtocol: 'h2', connectionInfo: 'h2' }; }, csi: function() { return { onloadT: Date.now(), startE: Date.now()-2000, pageT: 2000, tran: 15 }; } };
                const getP = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(p) { if (p===37445) return 'Google Inc. (NVIDIA)'; if (p===37446) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)'; return getP.call(this, p); };
                if (window.WebGL2RenderingContext) { const g2 = WebGL2RenderingContext.prototype.getParameter; WebGL2RenderingContext.prototype.getParameter = function(p) { if (p===37445) return 'Google Inc. (NVIDIA)'; if (p===37446) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)'; return g2.call(this, p); }; }
                Object.defineProperty(navigator, 'userAgentData', { get: () => ({ brands: [{brand:'Chromium',version:'136'},{brand:'Google Chrome',version:'136'},{brand:'Not.A/Brand',version:'99'}], mobile: false, platform: 'Windows', getHighEntropyValues: () => Promise.resolve({ brands: [{brand:'Chromium',version:'136'},{brand:'Google Chrome',version:'136'}], mobile: false, platform: 'Windows', platformVersion: '15.0.0', architecture: 'x86', bitness: '64', model: '', uaFullVersion: '136.0.7103.93' }) }) });
                Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
                Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
                Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
                Document.prototype.hasFocus = function() { return true; };
                Object.defineProperty(document, 'hidden', { get: () => false });
                Object.defineProperty(document, 'visibilityState', { get: () => 'visible' });
                if (window.outerWidth === 0) Object.defineProperty(window, 'outerWidth', { get: () => 1280 });
                if (window.outerHeight === 0) Object.defineProperty(window, 'outerHeight', { get: () => 885 });
                delete Object.getPrototypeOf(navigator).webdriver;
            """)
            # 清除旧cookie并注入新的登录cookie
            await context.clear_cookies()
            await context.add_cookies(cookies)
            page = context.pages[0] if context.pages else await context.new_page()

            # 先访问小红书首页让JS加载
            try:
                await page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
            await asyncio.sleep(2)

            any_success = False
            for uid in user_ids:
                self._status["progress"] = f"正在抓取 {uid}..."
                try:
                    # 1. 解析ObjectId（红薯号→ObjectId）
                    api_uid = await self._resolve_uid(uid, page)
                    if not api_uid:
                        logger.warning(f"XHS 无法解析用户 {uid}，跳过")
                        continue

                    # 2. 通过多策略获取笔记（__INITIAL_STATE__ → DOM提取 → API拦截）
                    notes = await self._intercept_profile_notes(
                        page,
                        api_uid,
                        login_account_id=account.id,
                    )
                    if notes:
                        source = "state_or_dom" if len(notes) > 0 else "api_intercept"
                        await self._save_notes(uid, notes, source)
                        self._status["progress"] = f"{uid} 获取 {len(notes)} 条笔记"
                        logger.info(f"XHS抓取成功: {uid} → {api_uid}, {len(notes)} 条")
                        any_success = True
                    else:
                        logger.warning(f"XHS {uid} 所有方式均未获取到数据（__INITIAL_STATE__ + DOM + API拦截）")

                    # ---- 同步监控账号昵称/头像/红薯号/个人简介 ----
                    try:
                        # 优先从DOM解析（最可靠，与页面实际显示一致）
                        user_info = await page.evaluate("""() => {
                            const info = {};
                            // 昵称: <div class="user-name">幸子</div>
                            const nameEl = document.querySelector('.user-name, [class*="user-name"]');
                            if (nameEl) {
                                info.nickname = (nameEl.textContent || '').trim();
                            }
                            // 红薯号: <span class="user-redId">小红书号：314689760</span>
                            const redIdEl = document.querySelector('.user-redId, [class*="user-redId"]');
                            if (redIdEl) {
                                const text = redIdEl.textContent || '';
                                const m = text.match(/[：:]\s*(\S+)/);
                                if (m) info.red_id = m[1];
                            }
                            // 头像: <img class="user-image" src="..."> (去掉模糊参数)
                            const avatarEl = document.querySelector('.user-image, [class*="user-image"]');
                            if (avatarEl) {
                                let src = avatarEl.getAttribute('src') || '';
                                src = src.split('|')[0];  // 去掉 |imageMogr2/strip2/blur/...
                                info.avatar = src;
                            }
                            // 个人简介: <div class="user-desc">🎵同名🐟幸子小姐</div>
                            const descEl = document.querySelector('.user-desc, [class*="user-desc"]');
                            if (descEl) {
                                info.desc = (descEl.textContent || '').trim();
                            }
                            // __INITIAL_STATE__ 兜底
                            if (!info.nickname || !info.red_id) {
                                try {
                                    const state = window.__INITIAL_STATE__;
                                    if (state) {
                                        let s = JSON.stringify(state).replace(/undefined/g, 'null');
                                        const parsed = JSON.parse(s);
                                        const u = parsed.user?.userPageData?.basicInfo || parsed.user?.userPageData || {};
                                        if (!info.nickname && (u.nickname || u.basicInfo?.nickname)) {
                                            info.nickname = u.nickname || u.basicInfo?.nickname;
                                        }
                                        if (!info.red_id && (u.redId || u.basicInfo?.redId)) {
                                            info.red_id = u.redId || u.basicInfo?.redId;
                                        }
                                        if (!info.avatar && (u.imageb || u.image || u.basicInfo?.imageb)) {
                                            info.avatar = u.imageb || u.image || u.basicInfo?.imageb || '';
                                        }
                                        if (!info.desc && (u.desc || u.basicInfo?.desc)) {
                                            info.desc = u.desc || u.basicInfo?.desc;
                                        }
                                    }
                                } catch(e) {}
                            }
                            return info;
                        }""")
                        if user_info and (user_info.get("nickname") or user_info.get("avatar")):
                            from app.services.media_service import media_service as ms_sync
                            async with async_session() as db_sync:
                                # 用uid匹配（可能是红薯号或ObjectId）
                                acct_r = await db_sync.execute(
                                    select(Account).where(
                                        Account.platform == "xhs",
                                        Account.account_id == uid,
                                        Account.is_target == 1,
                                    )
                                )
                                acct = acct_r.scalar_one_or_none()
                                # 也尝试用platform_uid匹配
                                if not acct:
                                    acct_r2 = await db_sync.execute(
                                        select(Account).where(
                                            Account.platform == "xhs",
                                            Account.platform_uid == api_uid,
                                            Account.is_target == 1,
                                        )
                                    )
                                    acct = acct_r2.scalar_one_or_none()
                                if acct:
                                    changed = []
                                    if user_info.get("nickname") and user_info["nickname"] != acct.nickname:
                                        # 记录昵称历史
                                        if acct.nickname:
                                            history = acct.nickname_history or []
                                            history.append({"nickname": acct.nickname, "time": datetime.now().isoformat()})
                                            acct.nickname_history = history
                                        acct.nickname = user_info["nickname"]
                                        changed.append(f"昵称={user_info['nickname']}")
                                    if user_info.get("red_id") and user_info["red_id"] != acct.account_id:
                                        acct.account_id = user_info["red_id"]
                                        changed.append(f"红薯号={user_info['red_id']}")
                                    if user_info.get("desc"):
                                        acct.signature = user_info["desc"]
                                        changed.append(f"简介={user_info['desc'][:20]}")
                                    if user_info.get("avatar"):
                                        avatar_local = await ms_sync.download_image(user_info["avatar"], "avatar")
                                        if avatar_local and avatar_local != acct.avatar_url:
                                            if acct.avatar_url:
                                                history = acct.avatar_history or []
                                                history.append({"url": acct.avatar_url, "time": datetime.now().isoformat()})
                                                acct.avatar_history = history
                                            acct.avatar_url = avatar_local
                                            changed.append("头像")
                                    if not acct.platform_uid:
                                        acct.platform_uid = api_uid
                                        changed.append(f"ObjectId={api_uid}")
                                    await db_sync.commit()
                                    if changed:
                                        logger.info(f"XHS 监控账号 {uid} 信息已同步: {', '.join(changed)}")
                                    else:
                                        logger.debug(f"XHS 监控账号 {uid} 信息无变化")
                    except Exception as e_info:
                        logger.debug(f"XHS 同步监控账号 {uid} 信息失败: {e_info}")

                    # 3. 逐条笔记: 获取原图+视频+评论（从主页上下文调用API）
                    if notes:
                        self._status["progress"] = f"{uid} 正在获取笔记原图/视频/评论..."
                        await self._enrich_notes_detail(
                            page,
                            uid,
                            notes,
                            api_uid=api_uid,
                            login_account_id=account.id,
                        )

                    await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"XHS抓取 {uid} 失败: {type(e).__name__}: {e}")

            return any_success
        except Exception as e:
            logger.error(f"XHS Playwright启动失败: {type(e).__name__}: {e}")
            return False
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            elif browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            if p:
                try:
                    await p.stop()
                except Exception:
                    pass

    async def _resolve_uid(self, uid: str, page) -> Optional[str]:
        """将用户输入的uid（红薯号或ObjectId）解析为API用的ObjectId"""
        from app.database import async_session
        from app.models.account import Account
        from sqlalchemy import select, and_

        # 1. 尝试从数据库获取已缓存的platform_uid
        async with async_session() as db:
            # 先精确匹配account_id
            result = await db.execute(
                select(Account).where(and_(
                    Account.platform == "xhs",
                    Account.account_id == uid,
                    Account.is_target == 1,
                ))
            )
            account = result.scalars().first()
            if account and account.platform_uid:
                logger.info(f"XHS {uid} → 已缓存ObjectId {account.platform_uid}")
                return account.platform_uid

            # 也可能uid直接是platform_uid
            if not account:
                result2 = await db.execute(
                    select(Account).where(and_(
                        Account.platform == "xhs",
                        Account.platform_uid == uid,
                        Account.is_target == 1,
                    ))
                )
                account2 = result2.scalars().first()
                if account2:
                    logger.info(f"XHS {uid} 本身就是ObjectId，直接匹配到账号 {account2.account_id}")
                    return uid

            # 日志：缓存未命中的原因
            if account:
                logger.info(f"XHS {uid} 在DB中存在但platform_uid为空: {account.platform_uid!r}")
            else:
                logger.info(f"XHS {uid} 在DB中未找到匹配的is_target=1账号")

        # 2. 如果已经是ObjectId格式（24位hex），直接使用
        if re.match(r'^[a-f0-9]{24}$', uid):
            return uid

        # 3. 红薯号 → ObjectId：通过搜索页面拦截解析
        logger.info(f"XHS uid={uid} 看起来是红薯号，通过搜索页面解析ObjectId...")
        captured_users = []

        async def on_search_response(response):
            url = response.url
            if 'search' in url and response.status == 200:
                try:
                    body = await response.json()
                    captured_users.append(body)
                except Exception:
                    pass

        page.on('response', on_search_response)
        try:
            # 导航到搜索页面
            try:
                await page.goto(
                    f"https://www.xiaohongshu.com/search_result?keyword={uid}",
                    wait_until="domcontentloaded", timeout=15000,
                )
            except Exception:
                pass
            await asyncio.sleep(3)

            # 尝试点击"用户"搜索标签
            try:
                user_tab = page.locator('div:has-text("用户"):not(:has(div))')
                for i in range(await user_tab.count()):
                    el = user_tab.nth(i)
                    text = (await el.text_content() or "").strip()
                    if text == "用户":
                        await el.click()
                        await asyncio.sleep(3)
                        break
            except Exception:
                pass

            # 解析搜索API响应
            for body in captured_users:
                data = body.get("data", {})
                # 遍历所有可能的数据结构
                items = data.get("items", []) or data.get("user_list", []) or data.get("users", [])
                for item in items:
                    user_info = item.get("user_info", item.get("user", item))
                    if not isinstance(user_info, dict):
                        continue
                    red_id = str(user_info.get("red_id", ""))
                    user_id = user_info.get("user_id", "")
                    # 精确匹配红薯号
                    if red_id == str(uid) and user_id:
                        logger.info(f"XHS 红薯号 {uid} → ObjectId {user_id} (via search API)")
                        await self._cache_platform_uid(uid, user_id)
                        return user_id

            # 搜索API无精确匹配，尝试从DOM提取用户链接
            dom_result = await page.evaluate("""(targetId) => {
                const links = document.querySelectorAll('a[href*="/user/profile/"]');
                for (const link of links) {
                    const href = link.getAttribute('href') || link.href || '';
                    const m = href.match(/\\/user\\/profile\\/([a-f0-9]{24})/);
                    if (m) {
                        // 检查该用户条目是否包含目标红薯号
                        const parent = link.closest('[class*="user"]') || link.parentElement?.parentElement;
                        if (parent && parent.textContent && parent.textContent.includes(targetId)) {
                            return m[1];
                        }
                    }
                }
                // 无精确匹配就返回第一个结果（搜索结果中最相关的）
                for (const link of links) {
                    const href = link.getAttribute('href') || link.href || '';
                    const m = href.match(/\\/user\\/profile\\/([a-f0-9]{24})/);
                    if (m) return m[1];
                }
                return '';
            }""", uid)

            if dom_result:
                logger.info(f"XHS 红薯号 {uid} → ObjectId {dom_result} (via search DOM)")
                await self._cache_platform_uid(uid, dom_result)
                return dom_result

            logger.warning(f"XHS 无法将红薯号 {uid} 解析为ObjectId")
            return None
        finally:
            page.remove_listener('response', on_search_response)

    async def _cache_platform_uid(self, account_id: str, platform_uid: str):
        """将解析的ObjectId缓存到数据库Account.platform_uid"""
        from app.database import async_session
        from app.models.account import Account
        from sqlalchemy import select, and_

        async with async_session() as db:
            result = await db.execute(
                select(Account).where(and_(
                    Account.platform == "xhs",
                    Account.account_id == account_id,
                    Account.is_target == 1,
                ))
            )
            account = result.scalars().first()
            if account:
                account.platform_uid = platform_uid
                await db.commit()
                logger.info(f"XHS 已缓存 {account_id} → {platform_uid}")

    async def _intercept_profile_notes(
        self,
        page,
        object_id: str,
        *,
        login_account_id: int | None = None,
    ) -> list[dict]:
        """导航到用户主页，拦截XHS自身发出的API响应获取全部笔记"""
        captured_notes = []
        seen_note_ids = set()
        has_more = True
        cookie_invalid_detected = False

        async def on_profile_response(response):
            nonlocal has_more, cookie_invalid_detected
            url = response.url
            # 精确匹配用户笔记API（user_posted等）
            is_user_posted = any(kw in url for kw in (
                'user_posted', 'user/posted', '/api/sns/web/v1/user_posted',
                '/api/sns/web/v2/user_posted', 'note/by_user', 'by_user',
                'user/note', 'homefeed', '/api/sns/web/v1/homefeed',
            ))
            is_notes_api = is_user_posted
            # 广泛捕获：所有XHS API且包含"note"关键词，但排除收藏/书签等干扰API
            if not is_notes_api and 'edith.xiaohongshu.com' in url and 'note' in url.lower():
                # 排除: collect(收藏)、unread(未读) 等非笔记列表API
                if 'collect' not in url.lower() and 'unread' not in url.lower():
                    is_notes_api = True
            if is_notes_api and response.status == 200:
                try:
                    body = await response.json()
                    if body.get("success") or body.get("data"):
                        data = body.get("data", {})
                        notes = data.get("notes", [])
                        if not notes and isinstance(data, list):
                            notes = data
                        new_count = 0
                        for note in notes:
                            nid = note.get("note_id") or note.get("id", "")
                            if nid and nid not in seen_note_ids:
                                seen_note_ids.add(nid)
                                captured_notes.append(note)
                                new_count += 1
                        # 只从 user_posted 精确接口更新 has_more（避免 collect/page 等干扰）
                        if is_user_posted:
                            if not data.get("has_more", True) or len(notes) == 0:
                                has_more = False
                        logger.info(f"XHS 拦截到 {new_count} 条新笔记 (累计 {len(captured_notes)}, has_more={has_more})")
                except Exception as e:
                    logger.debug(f"XHS 解析拦截响应失败: {e}")
            # 记录所有edith API请求用于调试
            if 'edith.xiaohongshu.com' in url and response.status == 200:
                logger.debug(f"XHS edith API: {url[:120]}")
            # 也拦截用户信息API
            if response.status == 200 and ('user/otherinfo' in url or 'selfinfo' in url or 'otherinfo' in url):
                try:
                    body = await response.json()
                    logger.debug(f"XHS 拦截到用户信息API: {url[:80]}")
                except Exception:
                    pass
            # 检测Cookie过期
            if response.status in (401, 461) and 'xiaohongshu.com' in url and not cookie_invalid_detected:
                cookie_invalid_detected = True
                logger.warning(f"XHS API返回 {response.status}，Cookie可能已过期")
                await self._mark_login_account_expired(
                    login_account_id,
                    reason=f"xhs_cookie_http_{response.status}",
                )
                await self._notify_cookie_expired("小红书", f"API返回HTTP {response.status}")

        page.on('response', on_profile_response)
        try:
            # 导航到用户主页
            profile_url = f"https://www.xiaohongshu.com/user/profile/{object_id}"
            logger.info(f"XHS 导航到用户主页: {profile_url}")
            try:
                await page.goto(profile_url, wait_until="networkidle", timeout=25000)
            except Exception:
                try:
                    await page.goto(profile_url, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
            await asyncio.sleep(5)

            # 记录当前页面信息用于调试
            try:
                page_title = await page.title()
                page_url = page.url
                logger.info(f"XHS 当前页面: title='{page_title}', url={page_url[:120]}")
                # 检测是否被重定向到验证码/登录页 → Cookie已过期，立即终止
                if 'captcha' in page_url or 'login' in page_url.lower() or page_title == '':
                    logger.warning(f"XHS Cookie已过期，页面被重定向到登录页: {page_url}")
                    self.login_status = "expired"
                    self.login_status_detail = "Cookie已过期，抓取时被重定向到登录页"
                    await self._mark_login_account_expired(
                        login_account_id,
                        reason="xhs_cookie_redirected_to_login",
                    )
                    asyncio.create_task(self._notify_cookie_expired("小红书", "抓取时页面被重定向到登录页"))
                    return []
                # 记录页面笔记数量用于诊断
                note_count_dom = await page.evaluate("""() => {
                    const cards = document.querySelectorAll('[class*="note-item"], section[class*="note-"], a[href*="/explore/"]');
                    return cards.length;
                }""")
                logger.info(f"XHS 页面DOM中检测到 {note_count_dom} 个笔记卡片元素")
            except Exception as e:
                logger.debug(f"XHS 页面检测异常: {e}")

            # ========== 优先策略1: 从__INITIAL_STATE__提取笔记（SSR页面最可靠） ==========
            if len(captured_notes) == 0:
                try:
                    state_notes = await self._extract_initial_state_notes(page)
                    if state_notes:
                        logger.info(f"XHS 从__INITIAL_STATE__提取到 {len(state_notes)} 条笔记，跳过API拦截")
                        return state_notes
                except Exception as e_state:
                    logger.debug(f"XHS __INITIAL_STATE__提取失败: {e_state}")

            # ========== 优先策略2: 从DOM直接提取笔记ID（广泛选择器） ==========
            if len(captured_notes) == 0:
                try:
                    dom_notes = await self._extract_notes_from_dom(page)
                    if dom_notes:
                        logger.info(f"XHS 从DOM直接提取到 {len(dom_notes)} 条笔记ID，跳过API拦截")
                        return dom_notes
                except Exception as e_dom:
                    logger.debug(f"XHS DOM提取失败: {e_dom}")

            # ========== 策略3: 点击"笔记"标签 + 滚动触发API拦截 ==========
            if len(captured_notes) == 0:
                try:
                    tabs = page.locator('[class*="tab"], [role="tab"]')
                    for i in range(await tabs.count()):
                        tab = tabs.nth(i)
                        text = (await tab.text_content() or "").strip()
                        if "笔记" in text:
                            await tab.click()
                            await asyncio.sleep(3)
                            logger.info("XHS 点击了'笔记'标签")
                            break
                except Exception:
                    pass

            # 如果还是0，尝试刷新页面
            if len(captured_notes) == 0:
                logger.info("XHS 初始加载无笔记，尝试刷新页面...")
                try:
                    await page.reload(wait_until="networkidle", timeout=20000)
                except Exception:
                    pass
                await asyncio.sleep(5)

            # 持续滚动加载全部笔记（XHS无限滚动 + cursor分页）
            max_scrolls = 30
            consecutive_no_new = 0
            for scroll_i in range(max_scrolls):
                prev_count = len(captured_notes)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)

                no_more = await page.evaluate("""() => {
                    const texts = document.body.innerText || '';
                    if (texts.includes('没有更多') || texts.includes('已经到底')) return true;
                    const els = document.querySelectorAll('[class*="no-more"], [class*="empty"], [class*="end-tip"], [class*="loading-end"]');
                    for (const el of els) {
                        if (el.offsetParent !== null && el.textContent && 
                            (el.textContent.includes('没有更多') || el.textContent.includes('到底了'))) return true;
                    }
                    return false;
                }""")
                if no_more or not has_more:
                    logger.info(f"XHS 到达页面底部 (scroll #{scroll_i+1}, 共{len(captured_notes)}条)")
                    break

                if len(captured_notes) == prev_count:
                    consecutive_no_new += 1
                    await asyncio.sleep(1.5)
                    if consecutive_no_new >= 4:
                        logger.info(f"XHS 连续{consecutive_no_new}次滚动无新数据，停止")
                        break
                else:
                    consecutive_no_new = 0

            logger.info(f"XHS API拦截完成: 共获取 {len(captured_notes)} 条笔记")

            # ========== 最终兜底: 再次尝试DOM提取 ==========
            if len(captured_notes) == 0:
                try:
                    dom_notes = await self._extract_notes_from_dom(page)
                    if dom_notes:
                        logger.info(f"XHS API拦截0条，DOM兜底提取到 {len(dom_notes)} 条笔记")
                        captured_notes = dom_notes
                except Exception as e_dom:
                    logger.debug(f"XHS DOM兜底提取失败: {e_dom}")

            return captured_notes
        finally:
            page.remove_listener('response', on_profile_response)

    async def _extract_initial_state_notes(self, page) -> list[dict]:
        """从页面的__INITIAL_STATE__提取笔记数据（SSR页面主要提取方式）"""
        result = await page.evaluate("""() => {
            try {
                if (!window.__INITIAL_STATE__) return [];
                // 清理undefined占位符（XHS的反序列化产物）
                let stateStr = JSON.stringify(window.__INITIAL_STATE__);
                stateStr = stateStr.replace(/undefined/g, 'null');
                const state = JSON.parse(stateStr);
                
                const notes = [];
                const seen = new Set();
                
                function addNote(n) {
                    const card = n.noteCard || n;
                    const nid = card.noteId || card.note_id || n.noteId || n.note_id || n.id || '';
                    if (nid && !seen.has(nid)) {
                        seen.add(nid);
                        notes.push({
                            note_id: nid,
                            display_title: card.displayTitle || card.display_title || card.title || '',
                            type: card.type || '',
                            cover: card.cover || {url: card.coverUrl || ''},
                            liked_count: card.interactInfo?.likedCount || card.liked_count || '',
                            user: card.user || n.user || {},
                        });
                    }
                }
                
                // ====== 路径1: user.userPageData.notes (最常见的XHS SSR结构) ======
                const userPageData = state.user?.userPageData;
                if (userPageData) {
                    // notes可能是数组
                    const ssr_notes = userPageData.notes;
                    if (Array.isArray(ssr_notes)) {
                        for (const n of ssr_notes) addNote(n);
                    }
                    // 也可能是对象 {noteId: noteCard, ...}
                    else if (ssr_notes && typeof ssr_notes === 'object') {
                        for (const [key, val] of Object.entries(ssr_notes)) {
                            if (val && typeof val === 'object') {
                                if (!val.note_id && !val.noteId) val.note_id = key;
                                addNote(val);
                            }
                        }
                    }
                }
                
                // ====== 路径2: state.user.notes / notesDetail ======
                for (const path of ['notes', 'notesDetail']) {
                    const arr = state.user?.[path];
                    if (Array.isArray(arr) && arr.length > 0) {
                        for (const n of arr) addNote(n);
                    }
                }
                
                // ====== 路径3: 深度搜索所有包含noteCard的对象 ======
                if (notes.length === 0) {
                    function findNotes(obj, depth) {
                        if (depth > 5 || !obj) return;
                        if (Array.isArray(obj)) {
                            for (const item of obj) {
                                if (item && typeof item === 'object') {
                                    if (item.noteCard || item.noteId || item.note_id) {
                                        addNote(item);
                                    } else {
                                        findNotes(item, depth + 1);
                                    }
                                }
                            }
                        } else if (typeof obj === 'object') {
                            for (const key of Object.keys(obj)) {
                                if (['router', 'common', 'config'].includes(key)) continue;
                                findNotes(obj[key], depth + 1);
                            }
                        }
                    }
                    findNotes(state, 0);
                }
                
                return notes;
            } catch(e) { console.error('XHS state extract error:', e); return []; }
        }""")
        if result:
            logger.info(f"XHS __INITIAL_STATE__ 提取到 {len(result)} 条笔记")
        else:
            logger.debug("XHS __INITIAL_STATE__ 未找到笔记数据")
        return result or []

    async def _extract_notes_from_dom(self, page) -> list[dict]:
        """从DOM直接提取笔记ID — 使用多种宽泛选择器策略"""
        dom_notes = await page.evaluate("""() => {
            const notes = [];
            const seen = new Set();
            
            function addNote(nid, el) {
                if (!nid || seen.has(nid)) return;
                seen.add(nid);
                const titleEl = el ? (
                    el.querySelector('[class*="title"], [class*="desc"], [class*="footer"] span') || 
                    el.closest('[class*="note-item"]')?.querySelector('[class*="title"], [class*="desc"]')
                ) : null;
                const coverEl = el ? (
                    el.querySelector('img') || 
                    el.closest('[class*="note-item"]')?.querySelector('img')
                ) : null;
                notes.push({
                    note_id: nid,
                    display_title: titleEl ? titleEl.textContent.trim() : '',
                    cover: {url: coverEl ? coverEl.src : ''},
                });
            }
            
            // 策略1: a标签中的note ID（/explore/, /discovery/, /search_result/）
            const links = document.querySelectorAll('a[href]');
            for (const link of links) {
                const href = link.getAttribute('href') || '';
                // 匹配 /explore/{24hex} 或 /search_result/{24hex} 或 /discovery/item/{24hex}
                const m = href.match(/(?:\\/explore\\/|\\/search_result\\/|\\/discovery\\/item\\/)([a-f0-9]{24})/);
                if (m) { addNote(m[1], link); continue; }
                // 匹配末尾的24位hex (如 /user/profile/uid/noteId)
                const m2 = href.match(/\\/([a-f0-9]{24})(?:[?#]|$)/);
                if (m2 && !href.includes('/user/profile/' + m2[1])) {
                    addNote(m2[1], link);
                }
            }
            
            // 策略2: note-item 容器元素（如果策略1没找到足够多的笔记）
            if (notes.length === 0) {
                const containers = document.querySelectorAll(
                    '[class*="note-item"], section[class*="note-"], [class*="feed-item"], [class*="note-card"]'
                );
                for (const container of containers) {
                    // 从容器内的a标签提取
                    const innerLink = container.querySelector('a[href]');
                    if (innerLink) {
                        const href = innerLink.getAttribute('href') || '';
                        const m = href.match(/([a-f0-9]{24})/);
                        if (m) { addNote(m[1], container); continue; }
                    }
                    // 从data属性提取
                    for (const attr of container.attributes) {
                        if (attr.name.startsWith('data-') && /^[a-f0-9]{24}$/.test(attr.value)) {
                            addNote(attr.value, container); break;
                        }
                    }
                }
            }
            
            // 策略3: 从页面HTML中正则提取所有潜在的note ID
            if (notes.length === 0) {
                const html = document.querySelector('[id="userPostedNotes"], [class*="feeds-page"], [id="content-area"]')?.innerHTML || '';
                if (html) {
                    const regex = /\\/explore\\/([a-f0-9]{24})/g;
                    let match;
                    while ((match = regex.exec(html)) !== null) {
                        addNote(match[1], null);
                    }
                }
            }
            
            return notes;
        }""")

        if dom_notes:
            logger.info(f"XHS DOM提取到 {len(dom_notes)} 条笔记ID")
        else:
            # 输出诊断信息帮助调试
            try:
                diag = await page.evaluate("""() => {
                    const noteItems = document.querySelectorAll('[class*="note-item"]');
                    const allLinks = document.querySelectorAll('a[href]');
                    const exploreLinks = document.querySelectorAll('a[href*="/explore/"]');
                    const sampleHrefs = [];
                    for (let i = 0; i < Math.min(5, allLinks.length); i++) {
                        const href = allLinks[i].getAttribute('href') || '';
                        if (href && href.length > 1 && !href.startsWith('javascript:')) {
                            sampleHrefs.push(href.substring(0, 80));
                        }
                    }
                    // 取note-item内部的链接样本
                    const noteItemLinks = [];
                    for (let i = 0; i < Math.min(3, noteItems.length); i++) {
                        const innerA = noteItems[i].querySelector('a[href]');
                        if (innerA) noteItemLinks.push((innerA.getAttribute('href') || '').substring(0, 80));
                        noteItemLinks.push('classes: ' + noteItems[i].className.substring(0, 60));
                    }
                    return {
                        noteItemCount: noteItems.length,
                        allLinksCount: allLinks.length,
                        exploreLinksCount: exploreLinks.length,
                        noteItemLinks: noteItemLinks,
                        sampleHrefs: sampleHrefs,
                        hasInitialState: !!window.__INITIAL_STATE__,
                    };
                }""")
                logger.warning(f"XHS DOM提取0条，诊断信息: noteItems={diag.get('noteItemCount')}, "
                             f"links={diag.get('allLinksCount')}, exploreLinks={diag.get('exploreLinksCount')}, "
                             f"hasState={diag.get('hasInitialState')}, "
                             f"noteItemLinks={diag.get('noteItemLinks', [])[:3]}")
            except Exception:
                pass

        return dom_notes or []

    async def _crawl_via_playwright(
        self,
        user_ids: list[str],
        *,
        login_account_id: int | None = None,
        allow_degraded_login: bool = False,
    ):
        """通过Playwright浏览器自动化抓取小红书"""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self._status["error"] = "Playwright未安装"
            return

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'],
            )

            account = await self._load_login_account(
                login_account_id,
                require_cookies=False,
                allow_degraded=allow_degraded_login,
            )
            cookies = None
            if account and account.cookies:
                cookies = json.loads(account.cookies)

            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                extra_http_headers={
                    'Sec-CH-UA': '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
                    'Sec-CH-UA-Mobile': '?0',
                    'Sec-CH-UA-Platform': '"Windows"',
                },
            )
            # 注入反检测脚本
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { app: { isInstalled: false }, runtime: { id: undefined }, loadTimes: function() { return {}; }, csi: function() { return {}; } };
                delete Object.getPrototypeOf(navigator).webdriver;
            """)
            if cookies:
                await context.add_cookies(cookies)

            page = await context.new_page()

            for uid in user_ids:
                self._status["progress"] = f"正在抓取 {uid} 的小红书笔记..."
                try:
                    await page.goto(f"https://www.xiaohongshu.com/user/profile/{uid}", wait_until="networkidle", timeout=30000)
                    await asyncio.sleep(3)

                    notes_data = await page.evaluate("""
                        () => {
                            const notes = [];
                            document.querySelectorAll('.note-item, [class*="note-card"]').forEach(item => {
                                try {
                                    const title = item.querySelector('.title, [class*="title"]')?.innerText || '';
                                    const cover = item.querySelector('img')?.src || '';
                                    const link = item.querySelector('a')?.href || '';
                                    const likes = item.querySelector('[class*="like"], .count')?.innerText || '0';
                                    if (title || cover) {
                                        notes.push({ title, cover, link, likes: parseInt(likes) || 0 });
                                    }
                                } catch(e) {}
                            });
                            return notes;
                        }
                    """)

                    # 逐个打开笔记获取详情
                    for note_brief in notes_data[:20]:
                        if not note_brief.get("link"):
                            continue
                        try:
                            await page.goto(note_brief["link"], wait_until="networkidle", timeout=20000)
                            await asyncio.sleep(2)

                            detail = await page.evaluate("""
                                () => {
                                    const content = document.querySelector('.desc, [class*="content"]')?.innerText || '';
                                    const nickname = document.querySelector('.username, [class*="author"]')?.innerText || '';
                                    const avatar = document.querySelector('.avatar img, [class*="avatar"] img')?.src || '';
                                    const images = [];
                                    document.querySelectorAll('.slide-item img, [class*="image"] img').forEach(img => {
                                        if (img.src && !img.src.includes('avatar')) images.push(img.src);
                                    });
                                    const tags = [];
                                    document.querySelectorAll('.tag, [class*="tag"] a').forEach(t => {
                                        if (t.innerText) tags.push(t.innerText.replace('#', ''));
                                    });
                                    return { content, nickname, avatar, images, tags };
                                }
                            """)

                            note_data = {**note_brief, **detail}
                            await self._save_single_note(uid, note_data)
                        except Exception as e:
                            logger.error(f"获取笔记详情失败: {e}")

                    self._status["progress"] = f"{uid} 抓取完成"
                except Exception as e:
                    logger.error(f"抓取 {uid} 失败: {e}")

            await browser.close()

    async def _save_notes(self, uid: str, notes: list[dict], source: str):
        """保存API返回的笔记数据（支持增量更新和版本追踪）"""
        from app.database import async_session
        from app.models.xhs_post import XHSNote
        from app.services.media_service import media_service
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        overwrite = getattr(self, '_crawl_mode', 'incremental') == 'overwrite'

        async with async_session() as db:
            for note in notes:
                note_id = note.get("note_id") or note.get("id", "")
                if not note_id:
                    continue

                existing_result = await db.execute(select(XHSNote).where(XHSNote.note_id == note_id))
                existing_note = existing_result.scalar_one_or_none()

                # 提取笔记数据 — 兼容多种XHS API格式
                # API拦截格式: {note_id, display_title, desc, liked_count, ...}
                # __INITIAL_STATE__格式: {noteCard: {noteId, title, desc, ...}}
                # 嵌套格式: {note_card: {display_title, ...}}
                note_card = note.get("noteCard") or note.get("note_card") or note
                new_title = note_card.get("display_title", "") or note_card.get("title", "") or note.get("display_title", "") or note.get("title", "")
                new_content = note_card.get("desc", "") or note.get("desc", "") or note_card.get("content", "") or note.get("content", "")
                # 互动数据解析 — 支持嵌套interact_info和顶级字段，正确处理0值
                interact_info = note_card.get("interact_info") or note_card.get("interactInfo") or {}
                if not isinstance(interact_info, dict):
                    interact_info = {}
                new_like = self._parse_count(interact_info.get("liked_count") or interact_info.get("likedCount") or note_card.get("liked_count") or note.get("liked_count", 0))
                new_collect = self._parse_count(interact_info.get("collected_count") or interact_info.get("collectedCount") or note_card.get("collected_count") or note.get("collected_count", 0))
                new_comment = self._parse_count(interact_info.get("comment_count") or interact_info.get("commentCount") or note_card.get("comment_count") or note.get("comment_count", 0))
                new_share = self._parse_count(interact_info.get("share_count") or interact_info.get("shareCount") or note_card.get("share_count") or note.get("share_count", 0))
                new_time = None
                if note.get("time"):
                    try:
                        new_time = datetime.fromtimestamp(note["time"] / 1000)
                    except Exception:
                        pass
                last_update = None
                if note.get("last_update_time"):
                    try:
                        last_update = datetime.fromtimestamp(note["last_update_time"] / 1000)
                    except Exception:
                        pass

                if existing_note:
                    # 增量更新：检测内容变化并记录版本历史
                    changes = {}
                    if new_title and new_title != (existing_note.title or ""):
                        changes["title"] = {"old": existing_note.title, "new": new_title}
                    if new_content and new_content != (existing_note.content or ""):
                        changes["content"] = {"old": existing_note.content, "new": new_content}
                    if new_like != (existing_note.like_count or 0):
                        changes["like_count"] = {"old": existing_note.like_count, "new": new_like}
                    if new_collect != (existing_note.collect_count or 0):
                        changes["collect_count"] = {"old": existing_note.collect_count, "new": new_collect}
                    if new_comment != (existing_note.comment_count or 0):
                        changes["comment_count"] = {"old": existing_note.comment_count, "new": new_comment}

                    # 更新统计数据（总是更新）
                    existing_note.like_count = new_like
                    existing_note.collect_count = new_collect
                    existing_note.comment_count = new_comment
                    existing_note.share_count = new_share
                    if last_update:
                        existing_note.last_update_time = last_update
                    existing_note.raw_data = note

                    # 补充头像（如果之前为空或文件不存在）
                    avatar_missing = not existing_note.author_avatar
                    if existing_note.author_avatar and existing_note.author_avatar.startswith("/static/"):
                        fp = os.path.join(settings.static_dir, existing_note.author_avatar[len("/static/"):])
                        if not os.path.isfile(fp):
                            avatar_missing = True
                    if avatar_missing:
                        avatar_url = note.get("user", {}).get("avatar", "")
                        if avatar_url:
                            local_av = await media_service.download_image(avatar_url, "avatar")
                            if local_av:
                                existing_note.author_avatar = local_av

                    # 覆盖模式：重新下载所有图片（优先原始格式URLs）
                    if overwrite:
                        note_card_ow = note.get("noteCard") or note.get("note_card") or note
                        image_list_ow = note_card_ow.get("images_list") or note_card_ow.get("image_list") or note_card_ow.get("imageList") or note.get("images_list") or note.get("image_list") or note.get("imageList", [])
                        img_urls_ow = []
                        for img_ow in image_list_ow:
                            if not isinstance(img_ow, dict):
                                continue
                            # 优先原始格式URL（非WebP CDN）
                            original_ow = img_ow.get("url_default") or img_ow.get("urlDefault") or img_ow.get("url_pre") or img_ow.get("urlPre") or ""
                            if original_ow and "webp" not in original_ow.lower() and "!nd_dft" not in original_ow:
                                img_urls_ow.append(original_ow)
                                continue
                            # 次选：infoList中找非WebP URL
                            ow_info_list = img_ow.get("info_list") or img_ow.get("infoList") or []
                            ow_best = ""
                            if ow_info_list and isinstance(ow_info_list, list):
                                for ow_info in reversed(ow_info_list):
                                    if isinstance(ow_info, dict) and (ow_info.get("url") or ow_info.get("image_url")):
                                        url_ow = ow_info.get("url") or ow_info.get("image_url", "")
                                        if "webp" not in url_ow.lower():
                                            ow_best = url_ow
                                            break
                                if not ow_best:
                                    for ow_info in reversed(ow_info_list):
                                        if isinstance(ow_info, dict) and (ow_info.get("url") or ow_info.get("image_url")):
                                            ow_best = ow_info.get("url") or ow_info.get("image_url", "")
                                            break
                            if ow_best:
                                img_urls_ow.append(ow_best)
                            elif original_ow:
                                img_urls_ow.append(original_ow)
                            else:
                                u_ow = img_ow.get("url", "")
                                if u_ow:
                                    img_urls_ow.append(u_ow)
                        if not img_urls_ow:
                            cover_ow = note_card_ow.get("cover") or note.get("cover") or {}
                            if isinstance(cover_ow, dict) and cover_ow.get("url"):
                                img_urls_ow = [cover_ow["url"]]
                        if img_urls_ow:
                            new_imgs = await media_service.download_images(img_urls_ow, "xhs")
                            existing_note.images = new_imgs
                            flag_modified(existing_note, "images")
                            logger.info(f"XHS笔记 {note_id} 覆盖更新图片 {len(new_imgs)} 张")
                        # 覆盖更新内容
                        if new_title:
                            existing_note.title = new_title
                        if new_content:
                            existing_note.content = new_content
                    else:
                        # 修复图片：检查是否有images中local_path为null或文件不存在的
                        if existing_note.images:
                            repaired = False
                            new_images = []
                            for img_item in existing_note.images:
                                lp = img_item.get("local_path") if isinstance(img_item, dict) else None
                                file_missing = False
                                if isinstance(img_item, dict) and not lp:
                                    file_missing = True
                                elif isinstance(img_item, dict) and lp and lp.startswith("/static/"):
                                    fp = os.path.join(settings.static_dir, lp[len("/static/"):])
                                    if not os.path.isfile(fp):
                                        file_missing = True
                                if file_missing:
                                    img_url = img_item.get("url", "") if isinstance(img_item, dict) else ""
                                    if img_url:
                                        local_path = await media_service.download_image(img_url, "xhs")
                                        if local_path:
                                            img_item["local_path"] = local_path
                                            repaired = True
                                    new_images.append(img_item)
                                else:
                                    new_images.append(img_item)
                            if repaired:
                                existing_note.images = new_images
                                flag_modified(existing_note, "images")

                    # 补充昵称
                    if not existing_note.author_nickname:
                        nick = note.get("user", {}).get("nickname", "")
                        if nick:
                            existing_note.author_nickname = nick

                    # 补充新字段（note_type/ip_location/at_user_list等）
                    if not existing_note.note_type:
                        nt = note_card.get("type", "") or note.get("type", "")
                        if nt:
                            existing_note.note_type = nt
                    if not existing_note.ip_location:
                        ip_loc = note_card.get("ip_location", "") or note_card.get("ipLocation", "") or note.get("ip_location", "")
                        if ip_loc:
                            existing_note.ip_location = ip_loc
                    if not existing_note.at_user_list:
                        at_list = note_card.get("at_user_list") or note_card.get("atUserList") or []
                        if at_list:
                            existing_note.at_user_list = [
                                {"user_id": u.get("user_id", "") or u.get("userId", ""), "nickname": u.get("nickname", ""), "avatar": u.get("avatar", "")}
                                for u in at_list if isinstance(u, dict)
                            ]

                    # 内容变化时记录历史
                    content_changed = "title" in changes or "content" in changes
                    if content_changed:
                        history = existing_note.edit_history or []
                        history.append({
                            "time": datetime.now().isoformat(),
                            "changes": changes,
                        })
                        existing_note.edit_history = history
                        if new_title:
                            existing_note.title = new_title
                        if new_content:
                            existing_note.content = new_content
                        logger.info(f"XHS笔记 {note_id} 内容已更新: {list(changes.keys())}")

                    continue  # 已更新现有记录

                # 新笔记：创建
                # 从多种可能的字段路径提取图片列表
                image_list = note_card.get("images_list") or note_card.get("image_list") or note_card.get("imageList") or note.get("images_list") or note.get("image_list") or note.get("imageList", [])
                image_urls = []
                for img in image_list:
                    if not isinstance(img, dict):
                        continue
                    # 优先原始格式URL（非WebP CDN）
                    original = img.get("url_default") or img.get("urlDefault") or img.get("url_pre") or img.get("urlPre") or ""
                    if original and "webp" not in original.lower() and "!nd_dft" not in original:
                        image_urls.append(original)
                        continue
                    # 次选：infoList中找非WebP URL
                    info_list = img.get("info_list") or img.get("infoList") or []
                    best = ""
                    if info_list and isinstance(info_list, list):
                        for info in reversed(info_list):
                            if isinstance(info, dict) and (info.get("url") or info.get("image_url")):
                                url_v = info.get("url") or info.get("image_url", "")
                                if "webp" not in url_v.lower():
                                    best = url_v
                                    break
                        if not best:
                            for info in reversed(info_list):
                                if isinstance(info, dict) and (info.get("url") or info.get("image_url")):
                                    best = info.get("url") or info.get("image_url", "")
                                    break
                    if best:
                        image_urls.append(best)
                    elif original:
                        image_urls.append(original)
                    else:
                        u = img.get("url", "")
                        if u:
                            image_urls.append(u)
                # 兜底: 从cover提取
                if not image_urls:
                    cover = note_card.get("cover") or note.get("cover") or {}
                    if isinstance(cover, dict):
                        # 优先从infoList取大图
                        cover_info = cover.get("info_list") or cover.get("infoList") or []
                        if cover_info and isinstance(cover_info, list):
                            for ci in reversed(cover_info):
                                if isinstance(ci, dict) and (ci.get("url") or ci.get("image_url")):
                                    image_urls.append(ci.get("url") or ci.get("image_url", ""))
                                    break
                        if not image_urls:
                            cover_url = cover.get("url") or cover.get("urlDefault", "")
                            if cover_url:
                                image_urls = [cover_url]

                images = await media_service.download_images(image_urls, "xhs")
                
                # 提取用户信息 — 兼容API格式(user)和SSR格式(noteCard.user)
                user_info = note_card.get("user") or note.get("user") or note_card.get("noteUser") or {}
                author_nick = user_info.get("nickname", "") or user_info.get("nickName", "") or user_info.get("nick_name", "")
                author_avatar_url = user_info.get("avatar", "") or user_info.get("image", "") or user_info.get("imageb", "")
                author_uid_val = user_info.get("user_id", "") or user_info.get("userId", "") or uid
                
                avatar_path = await media_service.download_image(author_avatar_url, "avatar") if author_avatar_url else None

                xhs_note = XHSNote(
                    xhs_uid=uid,
                    note_id=note_id,
                    note_type=note_card.get("type", "") or note.get("type", ""),
                    author_uid=author_uid_val,
                    author_nickname=author_nick,
                    author_avatar=avatar_path,
                    title=new_title,
                    content=new_content,
                    images=images,
                    video_url=note_card.get("video", {}).get("url", "") if note_card.get("video") else (note.get("video", {}).get("url", "") if note.get("video") else None),
                    tags=[t.get("name", "") for t in (note_card.get("tag_list") or note_card.get("tagList") or note.get("tag_list") or note.get("tagList") or []) if isinstance(t, dict) and t.get("name")],
                    at_user_list=[
                        {"user_id": u.get("user_id", "") or u.get("userId", ""), "nickname": u.get("nickname", ""), "avatar": u.get("avatar", "")}
                        for u in (note_card.get("at_user_list") or note_card.get("atUserList") or []) if isinstance(u, dict)
                    ] or None,
                    like_count=new_like,
                    collect_count=new_collect,
                    comment_count=new_comment,
                    share_count=new_share,
                    post_time=new_time or datetime.now(),
                    last_update_time=last_update,
                    note_url=f"https://www.xiaohongshu.com/explore/{note_id}",
                    ip_location=note_card.get("ip_location", "") or note_card.get("ipLocation", "") or note.get("ip_location", "") or "",
                    location=note_card.get("location", "") or note.get("location", "") or "",  # location是用户设置的地理位置，不是IP属地
                    raw_data=note,
                )
                db.add(xhs_note)

            await db.commit()

    async def _save_single_note(self, uid: str, note_data: dict):
        """保存Playwright抓取的单条笔记"""
        from app.database import async_session
        from app.models.xhs_post import XHSNote
        from app.services.media_service import media_service
        from sqlalchemy import select

        link = note_data.get("link", "")
        note_id = link.split("/")[-1].split("?")[0] if link else uuid.uuid4().hex[:16]

        async with async_session() as db:
            existing = await db.execute(select(XHSNote).where(XHSNote.note_id == note_id))
            if existing.scalar_one_or_none():
                return

            images = await media_service.download_images(note_data.get("images", []), "xhs")
            avatar_path = await media_service.download_image(note_data.get("avatar", ""), "avatar")

            xhs_note = XHSNote(
                xhs_uid=uid,
                note_id=note_id,
                author_uid=uid,
                author_nickname=note_data.get("nickname", ""),
                author_avatar=avatar_path,
                title=note_data.get("title", ""),
                content=note_data.get("content", ""),
                images=images,
                tags=note_data.get("tags", []),
                like_count=note_data.get("likes", 0),
                post_time=datetime.now(),
                note_url=link or f"https://www.xiaohongshu.com/explore/{note_id}",
                raw_data=note_data,
            )
            db.add(xhs_note)
            await db.commit()

    async def _enrich_notes_detail(
        self,
        page,
        uid: str,
        notes: list[dict],
        api_uid: str = None,
        login_account_id: int | None = None,
    ):
        """通过XHR API调用获取笔记原图/视频/评论，更新数据库"""
        from app.database import async_session
        from app.models.xhs_post import XHSNote
        from app.services.media_service import media_service
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        note_ids = []
        for note in notes:
            nid = note.get("note_id") or note.get("id", "")
            if nid:
                note_ids.append(nid)

        if not note_ids:
            return

        # 确保停留在个人主页（XHS JS签名上下文），不直接导航到笔记页
        profile_uid = api_uid or uid
        profile_url = f"https://www.xiaohongshu.com/user/profile/{profile_uid}"
        current_url = page.url or ""
        if f"/user/profile/{profile_uid}" not in current_url:
            try:
                await page.goto(profile_url, wait_until="networkidle", timeout=15000)
            except Exception:
                try:
                    await page.goto(profile_url, wait_until="domcontentloaded", timeout=10000)
                except Exception:
                    pass
            await asyncio.sleep(2)

        # 关键检查: 验证是否真正在个人主页上（而非被重定向到登录/验证码页面）
        actual_url = page.url or ""
        if "/user/profile/" not in actual_url:
            logger.error(f"XHS {uid} 无法访问个人主页(被重定向到 {actual_url[:120]}), "
                         f"Cookie可能已过期或被风控, 跳过全部 {len(note_ids)} 条笔记详情获取")
            return

        logger.info(f"XHS {uid} 已在个人主页({actual_url[:60]}), 开始获取 {len(note_ids)} 条笔记详情")

        success_count = 0
        auth_fail_count = 0  # 连续-101失败计数
        for idx, note_id in enumerate(note_ids):
            # 每次处理前检查页面状态: 如果被重定向到登录/验证码页则终止循环
            cur_page_url = page.url or ""
            if any(kw in cur_page_url for kw in ("login", "captcha", "/404")):
                logger.error(f"XHS {uid} 页面已跳转到 {cur_page_url[:100]}, "
                             f"会话可能失效, 中止详情获取 ({success_count} 成功, 剩余 {len(note_ids)-idx} 跳过)")
                break
            # Cookie过期连续3次后停止浪费时间
            if auth_fail_count >= 3:
                logger.error(f"XHS {uid} 连续{auth_fail_count}次Cookie过期(-101), "
                             f"小红书登录Cookie已失效, 请重新扫码登录! "
                             f"剩余 {len(note_ids)-idx} 条笔记跳过")
                self._status["error"] = "小红书Cookie已过期，请重新扫码登录"
                await self._mark_login_account_expired(
                    login_account_id,
                    reason="xhs_cookie_detail_auth_failed",
                )
                await self._notify_cookie_expired("小红书", "笔记详情接口连续返回登录失效")
                break
            try:
                logger.info(f"XHS {uid} 处理详情 {idx+1}/{len(note_ids)}: {note_id}")
                self._status["progress"] = f"{uid} 获取笔记详情 {idx+1}/{len(note_ids)}"
                detail, comments, auth_failed = await self._fetch_note_detail_and_comments(page, note_id)
                if auth_failed:
                    auth_fail_count += 1
                else:
                    auth_fail_count = 0  # 重置计数
                logger.info(f"XHS {note_id} 详情结果: 类型={detail.get('note_type','?') if detail else '?'}, images={len(detail.get('images',[])) if detail else 0}, comments={len(comments)}, video={'有' if detail and detail.get('video_url') else '无'}, like={detail.get('like_count','?') if detail else '?'}, IP={detail.get('ip_location','?') if detail else '?'}")

                async with async_session() as db:
                    result = await db.execute(select(XHSNote).where(XHSNote.note_id == note_id))
                    db_note = result.scalar_one_or_none()
                    if not db_note:
                        logger.warning(f"XHS {note_id} 不在数据库中，跳过详情更新（_save_notes可能未保存此笔记）")
                        continue

                    updated = False

                    # 更新原图（从笔记详情API获取的全分辨率图）— 总是替换以获取高清版
                    if detail and detail.get("images"):
                        hi_res_urls = detail["images"]
                        if hi_res_urls:
                            downloaded = await media_service.download_images(hi_res_urls, "xhs")
                            any_ok = any(d.get("local_path") for d in downloaded)
                            if any_ok:
                                db_note.images = downloaded
                                flag_modified(db_note, "images")
                                updated = True
                                logger.info(f"XHS {note_id} 更新原图 {len(downloaded)} 张")

                    # 更新视频（覆盖模式下总是重新下载）
                    overwrite = getattr(self, '_crawl_mode', 'incremental') == 'overwrite'
                    if detail and detail.get("video_url"):
                        video_url = detail["video_url"]
                        if video_url and (not db_note.local_video_path or overwrite):
                            db_note.video_url = video_url
                            local_video = await media_service.download_video(video_url, "xhs")
                            if local_video:
                                db_note.local_video_path = local_video
                                updated = True
                                logger.info(f"XHS {note_id} 下载视频成功")

                    # 补充详情数据（标题/内容/标签/互动/IP属地/类型等）
                    if detail:
                        if detail.get("title") and not db_note.title:
                            db_note.title = detail["title"]
                            updated = True
                        if detail.get("content") and not db_note.content:
                            db_note.content = detail["content"]
                            updated = True
                        if detail.get("tags") and not db_note.tags:
                            db_note.tags = detail["tags"]
                            flag_modified(db_note, "tags")
                            updated = True

                        # 互动数据（总是更新为最新值，注意0也是有效值）
                        if "like_count" in detail:
                            db_note.like_count = detail["like_count"]
                        if "collect_count" in detail:
                            db_note.collect_count = detail["collect_count"]
                        if "comment_count" in detail:
                            db_note.comment_count = detail["comment_count"]
                        if "share_count" in detail:
                            db_note.share_count = detail["share_count"]

                        # 笔记类型 (video / normal)
                        if detail.get("note_type") and not db_note.note_type:
                            db_note.note_type = detail["note_type"]
                            updated = True

                        # IP属地
                        if detail.get("ip_location") and not db_note.ip_location:
                            db_note.ip_location = detail["ip_location"]
                            updated = True
                        # location是用户发布位置，不从ip_location填充
                        if detail.get("location") and not db_note.location:
                            db_note.location = detail["location"]
                            updated = True

                        # 视频时长
                        if detail.get("video_duration") and not db_note.video_duration:
                            db_note.video_duration = detail["video_duration"]
                            updated = True

                        # @用户列表
                        if detail.get("at_user_list") and not db_note.at_user_list:
                            db_note.at_user_list = detail["at_user_list"]
                            flag_modified(db_note, "at_user_list")
                            updated = True

                        # 发布时间（如果之前为空或为默认值）
                        if detail.get("post_time") and (not db_note.post_time or db_note.post_time == datetime(1970, 1, 1)):
                            try:
                                ts = detail["post_time"]
                                db_note.post_time = datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts)
                                updated = True
                            except Exception:
                                pass

                        # 笔记最后修改时间
                        if detail.get("last_update_time"):
                            try:
                                ts = detail["last_update_time"]
                                db_note.last_update_time = datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts)
                                updated = True
                            except Exception:
                                pass

                        # 保存raw_data (note_card原始数据)
                        if detail.get("raw_note_card"):
                            db_note.raw_data = detail["raw_note_card"]
                            flag_modified(db_note, "raw_data")
                            updated = True

                        # 更新作者昵称和头像（修正错误/缺失的用户信息）
                        if detail.get("author_nickname") and (not db_note.author_nickname or db_note.author_nickname != detail["author_nickname"]):
                            db_note.author_nickname = detail["author_nickname"]
                            updated = True
                        if detail.get("author_uid") and (not db_note.author_uid or db_note.author_uid == uid):
                            db_note.author_uid = detail["author_uid"]
                            updated = True
                        if detail.get("author_avatar"):
                            # 下载新头像
                            avatar_url = detail["author_avatar"]
                            need_avatar = not db_note.author_avatar
                            if db_note.author_avatar and db_note.author_avatar.startswith("/static/"):
                                fp = os.path.join(settings.static_dir, db_note.author_avatar[len("/static/"):])
                                if not os.path.isfile(fp):
                                    need_avatar = True
                            if need_avatar:
                                local_av = await media_service.download_image(avatar_url, "avatar")
                                if local_av:
                                    db_note.author_avatar = local_av
                                    updated = True

                    if updated:
                        await db.commit()

                # 保存评论
                if comments:
                    await self._save_comments(note_id, comments)
                    logger.info(f"XHS {note_id} 保存 {len(comments)} 条评论")

                # 只有成功获取到数据才计数
                if detail and (detail.get("images") or detail.get("title") or detail.get("video_url")):
                    success_count += 1
                await asyncio.sleep(3)  # 增大间隔减少风控风险
            except Exception as e:
                logger.error(f"XHS 获取笔记 {note_id} 详情失败: {type(e).__name__}: {e}")
            finally:
                # 确保页面干净地回到个人主页（每次都reload以确保DOM完整）
                try:
                    cur = page.url or ""
                    if "/user/profile/" not in cur:
                        # 页面被导航走了，必须回到个人主页
                        await page.goto(profile_url, wait_until="networkidle", timeout=15000)
                        await asyncio.sleep(2)
                    else:
                        # 仍在主页URL，但DOM可能被弹窗/导航破坏（React状态脏了）
                        # 按Escape关闭可能的弹窗
                        try:
                            await page.keyboard.press("Escape")
                            await asyncio.sleep(0.3)
                        except Exception:
                            pass
                except Exception:
                    pass

        logger.info(f"XHS {uid} 笔记详情获取完成: {success_count}/{len(note_ids)} 条成功")

    async def _fetch_note_detail_and_comments(self, page, note_id: str) -> tuple[dict | None, list[dict], bool]:
        """访问笔记详情页，同时拦截笔记详情API和评论API
        
        返回 (detail_dict, comments_list, feed_auth_failed)
        detail_dict: {"images": [...full_res_urls], "video_url": str, "title": str, ...}
        feed_auth_failed: True表示feed API返回-101(Cookie过期)
        """
        detail_data = {}
        captured_comments = []
        seen_comment_ids = set()
        feed_auth_failed = False  # feed API返回-101（Cookie过期）

        async def on_response(response):
            nonlocal feed_auth_failed
            url = response.url
            # Debug: log all API calls during detail page load
            if 'edith' in url or '/api/' in url:
                logger.debug(f"XHS detail-api: {url[:200]} status={response.status}")
            # 拦截笔记详情API（feed接口）
            is_feed_api = any(kw in url for kw in (
                '/api/sns/web/v1/feed',
                '/api/sns/web/v2/note',
                '/api/sns/web/v1/note',
            ))
            if is_feed_api and response.status == 200:
                try:
                    body = await response.json()
                    # 先检查API业务状态码
                    api_code = body.get("code", -999)
                    api_success = body.get("success", None)
                    data = body.get("data", {})

                    # ===== DEBUG: 输出原始响应结构帮助诊断 =====
                    body_keys = list(body.keys()) if isinstance(body, dict) else type(body).__name__
                    data_keys = list(data.keys())[:15] if isinstance(data, dict) else (f"list[{len(data)}]" if isinstance(data, list) else type(data).__name__)
                    logger.debug(f"XHS feed响应结构 {note_id}: code={api_code}, success={api_success}, "
                                 f"body_keys={body_keys}, data_keys={data_keys}")

                    if api_code != 0 and api_success is not True:
                        # API返回业务错误（HTTP 200但code!=0）
                        logger.warning(f"XHS feed API业务错误 {note_id}: code={api_code}, msg={body.get('msg','')}, "
                                       f"data_preview={str(data)[:200]}")
                        if api_code == -101:
                            feed_auth_failed = True
                        # 不return/continue，仍然尝试解析（有些接口没有code字段）

                    # v1/feed 返回 data.items[0].note_card 或 data[0].note_card
                    items = data.get("items", [data]) if isinstance(data, dict) else data if isinstance(data, list) else []
                    if items:
                        item0_keys = list(items[0].keys())[:15] if isinstance(items[0], dict) else type(items[0]).__name__
                        logger.debug(f"XHS feed item0结构 {note_id}: item_keys={item0_keys}")
                        if isinstance(items[0], dict) and "note_card" in items[0]:
                            nc_keys = list(items[0]["note_card"].keys())[:20] if isinstance(items[0]["note_card"], dict) else "?"
                            logger.debug(f"XHS feed note_card结构 {note_id}: keys={nc_keys}")

                    for item in items:
                        note_card = item.get("note_card", item)
                        target_id = item.get("id", "") or note_card.get("note_id", "")
                        if target_id and target_id != note_id:
                            continue

                        # 保存原始note_card
                        detail_data["raw_note_card"] = note_card

                        # ========== 提取全分辨率原图（优先原始格式URL，非WebP） ==========
                        image_list = note_card.get("image_list", [])
                        logger.debug(f"XHS feed提取 {note_id}: image_list={len(image_list)}, "
                                     f"title={note_card.get('title','')[:30]}, type={note_card.get('type','')}, "
                                     f"has_video={'video' in note_card}, has_interact={'interact_info' in note_card}")
                        hi_res_images = []
                        for img in image_list:
                            # 优先使用 url_default（原始格式CDN，非WebP）
                            original_url = img.get("url_default", "") or img.get("url_pre", "") or img.get("url", "")
                            if original_url and "webp" not in original_url.lower() and "!nd_dft" not in original_url:
                                hi_res_images.append(original_url)
                                continue
                            # 次选：info_list中找非WebP URL
                            info_list = img.get("info_list", [])
                            best = ""
                            if info_list:
                                for info_item in reversed(info_list):
                                    url = info_item.get("url", "") or info_item.get("image_url", "")
                                    if url and "webp" not in url.lower():
                                        best = url
                                        break
                                if not best:
                                    best = info_list[-1].get("url", "") or info_list[-1].get("image_url", "")
                            if best:
                                hi_res_images.append(best)
                            elif original_url:
                                hi_res_images.append(original_url)
                        if hi_res_images:
                            detail_data["images"] = hi_res_images

                        # ========== 提取视频URL + 时长 ==========
                        video = note_card.get("video", {})
                        if video and isinstance(video, dict):
                            video_url = ""
                            media = video.get("media", {})
                            if media:
                                stream = media.get("stream", {})
                                # 优先选高质量编码，每种编码内选最高清stream（id越大越高清）
                                for quality in ["h265", "h266", "av1", "h264"]:
                                    streams = stream.get(quality, [])
                                    if streams:
                                        # 选最高清的stream (按id排序取最大)
                                        best_stream = max(streams, key=lambda s: int(s.get("id", "0") or "0"))
                                        video_url = best_stream.get("master_url", "")
                                        if not video_url:
                                            bu = best_stream.get("backup_urls") or []
                                            video_url = bu[0] if bu else ""
                                        if video_url:
                                            break
                            if not video_url:
                                video_url = video.get("url", "")
                            if not video_url:
                                consumer = video.get("consumer", {})
                                if consumer:
                                    video_url = consumer.get("origin_video_key", "")
                            if video_url and video_url.startswith("http"):
                                detail_data["video_url"] = video_url
                            # 视频时长（毫秒→秒）
                            capa = video.get("capa", {})
                            if isinstance(capa, dict) and capa.get("duration"):
                                try:
                                    detail_data["video_duration"] = int(float(capa["duration"]))
                                except (ValueError, TypeError):
                                    pass

                        # ========== 笔记类型 ==========
                        detail_data["note_type"] = note_card.get("type", "")  # "video" / "normal"

                        # ========== 标题/内容 ==========
                        detail_data["title"] = note_card.get("title", "") or note_card.get("display_title", "")
                        detail_data["content"] = note_card.get("desc", "") or note_card.get("content", "")

                        # ========== 标签 ==========
                        tag_list = note_card.get("tag_list", [])
                        if tag_list:
                            detail_data["tags"] = [t.get("name", "") for t in tag_list if isinstance(t, dict) and t.get("name")]

                        # ========== @用户列表 ==========
                        at_user_list = note_card.get("at_user_list", [])
                        if at_user_list and isinstance(at_user_list, list):
                            detail_data["at_user_list"] = [
                                {"user_id": u.get("user_id", ""), "nickname": u.get("nickname", ""), "avatar": u.get("avatar", "")}
                                for u in at_user_list if isinstance(u, dict)
                            ]

                        # ========== 互动数据 ==========
                        interact = note_card.get("interact_info", note_card.get("interactInfo", {}))
                        if isinstance(interact, dict):
                            detail_data["like_count"] = self._parse_count(interact.get("liked_count") or interact.get("likedCount", 0))
                            detail_data["collect_count"] = self._parse_count(interact.get("collected_count") or interact.get("collectedCount", 0))
                            detail_data["comment_count"] = self._parse_count(interact.get("comment_count") or interact.get("commentCount", 0))
                            detail_data["share_count"] = self._parse_count(interact.get("share_count") or interact.get("shareCount", 0))
                        else:
                            detail_data["like_count"] = self._parse_count(note_card.get("liked_count", 0))

                        # ========== IP属地 ==========
                        ip_loc = note_card.get("ip_location", "")
                        if ip_loc:
                            detail_data["ip_location"] = ip_loc

                        # ========== 时间 ==========
                        note_time = note_card.get("time", 0)
                        if note_time:
                            try:
                                detail_data["post_time"] = int(note_time)
                            except (ValueError, TypeError):
                                pass
                        last_up = note_card.get("last_update_time", 0)
                        if last_up:
                            try:
                                detail_data["last_update_time"] = int(last_up)
                            except (ValueError, TypeError):
                                pass

                        # ========== 作者信息 ==========
                        note_user = note_card.get("user", {})
                        if isinstance(note_user, dict):
                            detail_data["author_nickname"] = note_user.get("nickname", "")
                            detail_data["author_avatar"] = note_user.get("avatar", "")
                            detail_data["author_uid"] = note_user.get("user_id", "")

                        logger.info(f"XHS 拦截到笔记详情: {note_id}, 类型={detail_data.get('note_type','?')}, 图片{len(hi_res_images)}张, 视频={'有' if detail_data.get('video_url') else '无'}, IP={detail_data.get('ip_location','?')}, 点赞={detail_data.get('like_count','?')}")
                except Exception as e:
                    logger.debug(f"XHS 解析笔记详情响应失败: {e}")

            # 拦截评论API
            is_comment_api = any(kw in url for kw in (
                '/api/sns/web/v2/comment/page',
                '/api/sns/web/v1/comment/page',
                'comment/page',
                'comment/sub/page',
            ))
            if is_comment_api and response.status == 200:
                try:
                    body = await response.json()
                    data = body.get("data", {})
                    comments = data.get("comments", [])
                    for c in comments:
                        cid = c.get("id", "")
                        if cid and cid not in seen_comment_ids:
                            seen_comment_ids.add(cid)
                            captured_comments.append(c)
                            for sc in c.get("sub_comments", []):
                                scid = sc.get("id", "")
                                if scid and scid not in seen_comment_ids:
                                    seen_comment_ids.add(scid)
                                    captured_comments.append({**sc, "_parent_comment_id": cid})
                except Exception as e:
                    logger.debug(f"XHS 解析评论响应失败: {e}")

        page.on('response', on_response)
        try:
            # 记录当前页面状态
            cur_url = page.url or ""
            logger.debug(f"XHS detail开始 {note_id}: 当前页面={cur_url[:80]}")

            # ========== 策略1(主): 用Playwright真实鼠标点击笔记卡片 → XHS弹窗 → feed API → on_response捕获 ==========
            # 关键: 必须用Playwright的mouse.click()发送trusted事件,React才会preventDefault阻止<a>导航并打开弹窗
            # JS的element.click()是synthetic事件,会触发<a>默认导航行为→note_info 461
            try:
                # Step1: 用JS找到卡片的可见cover容器元素（不是隐藏的<a>标签），获取坐标
                card_info = await page.evaluate("""(noteId) => {
                    // 用getAttribute匹配（和DOM提取一样），不用link.href（浏览器可能resolve结果不同）
                    const links = document.querySelectorAll('a[href]');
                    let targetLink = null;
                    for (const link of links) {
                        const rawHref = link.getAttribute('href') || '';
                        if (rawHref.includes(noteId)) {
                            targetLink = link;
                            break;
                        }
                    }
                    if (!targetLink) {
                        // 调试: 输出前5个explore链接的原始href
                        const exploreLinks = document.querySelectorAll('a[href*="/explore/"]');
                        const samples = Array.from(exploreLinks).slice(0, 5).map(a => a.getAttribute('href')?.substring(0, 80) || '');
                        return {found: false, exploreLinks: exploreLinks.length, samples: samples,
                                pageUrl: window.location.href.substring(0, 80)};
                    }
                    
                    // 找到链接，现在找可见的可点击元素
                    // XHS卡片结构: section > div.cover > a[href] + img
                    // <a>标签通常不可见（用于SEO），可见的是其父容器或兄弟图片
                    const card = targetLink.closest('section') || targetLink.closest('[class*="note"]') || targetLink.parentElement;
                    
                    // 找最佳点击目标：图片 > cover容器 > section > link本身
                    const candidates = [
                        card?.querySelector('img'),
                        card?.querySelector('[class*="cover"]'),
                        card,
                        targetLink
                    ];
                    
                    for (const el of candidates) {
                        if (!el) continue;
                        el.scrollIntoView({behavior: 'instant', block: 'center'});
                        // 等一帧让scrollIntoView生效
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 5 && rect.height > 5) {
                            return {
                                found: true,
                                x: rect.left + rect.width / 2,
                                y: rect.top + rect.height / 2,
                                w: rect.width, h: rect.height,
                                tag: el.tagName,
                                cls: (el.className || '').substring(0, 40),
                                href: targetLink.getAttribute('href')?.substring(0, 80)
                            };
                        }
                    }
                    
                    // 所有元素都不可见，返回链接信息供fallback
                    return {
                        found: true, noVisibleElement: true,
                        href: targetLink.getAttribute('href')?.substring(0, 80),
                        linkTag: targetLink.tagName,
                        parentTag: targetLink.parentElement?.tagName
                    };
                }""", note_id)

                if card_info and card_info.get("found"):
                    if card_info.get("noVisibleElement"):
                        # 所有元素不可见，用Playwright force-click <a>标签（发trusted event绕过visibility）
                        logger.debug(f"XHS click {note_id}: 卡片不可见, 尝试force-click, href={card_info.get('href')}")
                        try:
                            locator = page.locator(f'a[href*="{note_id}"]')
                            if await locator.count() > 0:
                                await locator.first.click(force=True, timeout=5000)
                            else:
                                # 精确匹配href属性
                                href_val = card_info.get("href", "")
                                if href_val:
                                    locator2 = page.locator(f'a[href="{href_val}"]')
                                    if await locator2.count() > 0:
                                        await locator2.first.click(force=True, timeout=5000)
                        except Exception as e:
                            logger.debug(f"XHS force-click {note_id}: {type(e).__name__}: {e}")
                    else:
                        # 有可见元素，用page.mouse真实点击（坐标是viewport相对的）
                        x, y = card_info["x"], card_info["y"]
                        logger.debug(f"XHS click {note_id}: {card_info.get('tag')}.{card_info.get('cls','')}, "
                                     f"pos=({x:.0f},{y:.0f}), size={card_info.get('w','?')}x{card_info.get('h','?')}")
                        await page.mouse.click(x, y)
                    
                    # 等待XHS打开笔记弹窗 → 自动发签名的feed API → on_response捕获
                    for _ in range(80):  # 最多16秒
                        if detail_data.get("images") or detail_data.get("title"):
                            break
                        # 如果检测到Cookie过期(-101)，提前结束等待
                        if feed_auth_failed:
                            await asyncio.sleep(1)  # 稍等确保评论API完成
                            break
                        await asyncio.sleep(0.2)
                    await asyncio.sleep(2)  # 额外等评论加载

                    # ===== DOM降级提取: Cookie过期时feed API无数据, 从弹窗DOM提取 =====
                    if feed_auth_failed and not detail_data.get("images") and not detail_data.get("title"):
                        try:
                            dom_detail = await page.evaluate("""() => {
                                const info = {};
                                // 弹窗/详情页中的笔记内容
                                // 标题: <div id="detail-title">, <span class="note-text">, 或 [class*="title"]
                                const titleEl = document.querySelector('#detail-title, [class*="note-title"], .note-text .title, [class*="title"][class*="note"]');
                                if (titleEl) info.title = (titleEl.textContent || '').trim();
                                // 备选标题: 弹窗内第一个大字号标题
                                if (!info.title) {
                                    const h1 = document.querySelector('[class*="detail"] h1, [class*="modal"] h1, [class*="note-detail"] [class*="title"]');
                                    if (h1) info.title = (h1.textContent || '').trim();
                                }
                                // 描述/内容
                                const descEl = document.querySelector('#detail-desc, [class*="note-text"] [class*="desc"], [class*="content"][class*="note"]');
                                if (descEl) info.content = (descEl.textContent || '').trim();
                                // 图片: 弹窗内的大图 (slider/swiper中的img)
                                const images = [];
                                const imgEls = document.querySelectorAll('[class*="swiper"] img, [class*="slider"] img, [class*="carousel"] img, [class*="note-detail"] img[src*="xhscdn"], [class*="media"] img[src*="xhscdn"]');
                                for (const img of imgEls) {
                                    let src = img.getAttribute('src') || img.src || '';
                                    if (src && src.includes('xhscdn') && !src.includes('avatar')) {
                                        // 去掉缩略参数, 获取原图
                                        src = src.split('?')[0].split('!')[0];
                                        if (!images.includes(src)) images.push(src);
                                    }
                                }
                                if (images.length) info.images = images;
                                // 互动数据: 点赞/收藏/评论数
                                const interactEls = document.querySelectorAll('[class*="interact"] [class*="count"], [class*="engage"] span, [class*="like"] [class*="count"]');
                                const counts = [];
                                for (const el of interactEls) {
                                    const t = (el.textContent || '').trim();
                                    if (t && /^[\\d.]+[万kK]?$/.test(t)) counts.push(t);
                                }
                                if (counts.length >= 1) info.interact_counts = counts;
                                // IP属地
                                const ipEl = document.querySelector('[class*="location"], [class*="ip"]');
                                if (ipEl) {
                                    const ipText = (ipEl.textContent || '').trim();
                                    if (ipText && ipText.length < 20) info.ip_location = ipText.replace(/^IP属地[：:]\\s*/, '');
                                }
                                // 笔记类型(检查是否有video标签)
                                const hasVideo = !!document.querySelector('[class*="note-detail"] video, [class*="player"] video, video[src]');
                                if (hasVideo) info.note_type = 'video';
                                return info;
                            }""")
                            if dom_detail:
                                if dom_detail.get("title") and not detail_data.get("title"):
                                    detail_data["title"] = dom_detail["title"]
                                if dom_detail.get("content") and not detail_data.get("content"):
                                    detail_data["content"] = dom_detail["content"]
                                if dom_detail.get("images") and not detail_data.get("images"):
                                    detail_data["images"] = dom_detail["images"]
                                if dom_detail.get("ip_location") and not detail_data.get("ip_location"):
                                    detail_data["ip_location"] = dom_detail["ip_location"]
                                if dom_detail.get("note_type") and not detail_data.get("note_type"):
                                    detail_data["note_type"] = dom_detail["note_type"]
                                dom_imgs = len(dom_detail.get("images", []))
                                logger.info(f"XHS DOM降级 {note_id}: title={dom_detail.get('title','')[:30]}, "
                                           f"images={dom_imgs}, ip={dom_detail.get('ip_location','')}")
                        except Exception as e:
                            logger.debug(f"XHS DOM降级提取失败 {note_id}: {e}")

                    logger.info(f"XHS click {note_id}: images={len(detail_data.get('images',[]))}, "
                                f"title={detail_data.get('title','')[:30]}, comments={len(captured_comments)}"
                                f"{', Cookie过期(-101)' if feed_auth_failed else ''}")
                    
                    # 关闭弹窗或从导航返回
                    try:
                        cur = page.url or ""
                        if "/explore/" in cur or "/discovery/" in cur:
                            # click触发了页面导航而不是弹窗，goBack恢复
                            await page.go_back(wait_until="domcontentloaded", timeout=8000)
                            await asyncio.sleep(1)
                        else:
                            await page.keyboard.press("Escape")
                            await asyncio.sleep(0.5)
                    except Exception:
                        pass
                else:
                    logger.warning(f"XHS click {note_id}: 未找到卡片, exploreLinks={card_info.get('exploreLinks')}, "
                                   f"samples={card_info.get('samples')}, page={card_info.get('pageUrl')}")
            except Exception as e:
                logger.warning(f"XHS click {note_id}: {type(e).__name__}: {e}")

            # ========== 策略2(备用): 手动fetch调feed API（签名可能不完整，406概率高） ==========
            if not detail_data.get("images") and not detail_data.get("title"):
                try:
                    feed_result = await page.evaluate("""async (noteId) => {
                        const apiPath = '/api/sns/web/v1/feed';
                        const payload = JSON.stringify({
                            source_note_id: noteId,
                            image_formats: ['jpg', 'webp', 'avif'],
                            extra: {need_body_topic: 1}
                        });
                        let headers = {
                            'Content-Type': 'application/json',
                            'Origin': 'https://www.xiaohongshu.com',
                            'Referer': 'https://www.xiaohongshu.com/'
                        };
                        // 尝试签名（_webmsxyw只生成X-s和X-t，缺少X-s-common，可能仍406）
                        let signResult = null;
                        if (typeof window._webmsxyw === 'function') {
                            try {
                                signResult = window._webmsxyw(apiPath, payload);
                                if (signResult) {
                                    Object.keys(signResult).forEach(k => { headers[k] = signResult[k]; });
                                }
                            } catch(e) { signResult = {error: e.message}; }
                        }
                        try {
                            const resp = await fetch('https://edith.xiaohongshu.com' + apiPath, {
                                method: 'POST', headers, body: payload, credentials: 'include'
                            });
                            const text = await resp.text();
                            return {status: resp.status, bodyLen: text.length, preview: text.substring(0,200),
                                    signed: !!signResult && !signResult.error};
                        } catch(e) { return {status: -1, error: e.message}; }
                    }""", note_id)
                    await asyncio.sleep(1.5)
                    logger.debug(f"XHS feed-fetch {note_id}: status={feed_result.get('status')}, "
                                 f"signed={feed_result.get('signed')}, images={len(detail_data.get('images',[]))}")
                except Exception as e:
                    logger.debug(f"XHS feed-fetch {note_id}: {type(e).__name__}: {e}")

            # [已移除策略3] 直接导航到/explore/{note_id}会触发安全拦截→跳转404/登录页
            # 并使cookie失效, 导致所有后续笔记全部失败, 已彻底删除

            # ========== 评论: 签名fetch调用评论API（兜底） ==========
            if not captured_comments:
                try:
                    comment_result = await page.evaluate("""async (noteId) => {
                        const apiPath = '/api/sns/web/v2/comment/page?note_id=' + noteId + '&cursor=&top_comment_id=&image_formats=jpg,webp,avif';
                        let headers = {
                            'Referer': 'https://www.xiaohongshu.com/'
                        };
                        if (typeof window._webmsxyw === 'function') {
                            try {
                                const sign = window._webmsxyw(apiPath, void 0);
                                if (sign) {
                                    if (sign['X-s']) headers['X-s'] = sign['X-s'];
                                    if (sign['X-t']) headers['X-t'] = sign['X-t'];
                                    if (sign['X-s-common']) headers['X-s-common'] = sign['X-s-common'];
                                }
                            } catch(e) {}
                        }
                        try {
                            const resp = await fetch('https://edith.xiaohongshu.com' + apiPath, {
                                headers: headers,
                                credentials: 'include'
                            });
                            return {status: resp.status};
                        } catch(e) {
                            return {status: -1, error: e.message};
                        }
                    }""", note_id)
                    await asyncio.sleep(1)
                    logger.debug(f"XHS comment-API {note_id}: status={comment_result.get('status')}, comments={len(captured_comments)}")
                except Exception:
                    pass

            return (detail_data if detail_data else None, captured_comments, feed_auth_failed)
        finally:
            page.remove_listener('response', on_response)

    async def crawl_single_note_comments(
        self,
        note_id: str,
        login_account_id: int | None = None,
        *,
        allow_degraded_login: bool = False,
    ) -> int:
        """为单条笔记抓取评论（由API endpoint调用），返回新增评论数"""
        account = await self._load_login_account(
            login_account_id,
            require_cookies=True,
            allow_degraded=allow_degraded_login,
        )
        if not account or not account.cookies:
            raise Exception("无可用的小红书登录Cookie")
        cookies = json.loads(account.cookies)

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise Exception("Playwright未安装")

        p = await async_playwright().start()
        browser = None
        try:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'])
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                extra_http_headers={
                    'Sec-CH-UA': '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
                    'Sec-CH-UA-Mobile': '?0',
                    'Sec-CH-UA-Platform': '"Windows"',
                },
            )
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { app: { isInstalled: false }, runtime: { id: undefined }, loadTimes: function() { return {}; }, csi: function() { return {}; } };
                delete Object.getPrototypeOf(navigator).webdriver;
            """)
            await context.add_cookies(cookies)
            page = await context.new_page()

            # 先访问首页加载JS
            try:
                await page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
            await asyncio.sleep(2)

            _, comments, _ = await self._fetch_note_detail_and_comments(page, note_id)
            if comments:
                await self._save_comments(note_id, comments)
                return len(comments)
            return 0
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            try:
                await p.stop()
            except Exception:
                pass

    async def _crawl_comments_for_notes(self, page, uid: str, notes: list[dict]):
        """为已爬取的笔记抓取评论（导航到笔记页面，拦截评论API）"""
        from app.database import async_session
        from app.models.xhs_post import XHSNote
        from sqlalchemy import select

        # 获取有评论的笔记ID列表
        note_ids_with_comments = []
        for note in notes:
            note_card = note.get("noteCard") or note.get("note_card") or note
            nid = note.get("note_id") or note.get("id", "")
            comment_count = note_card.get("comment_count", 0) or note.get("comment_count", 0) or 0
            if nid and comment_count > 0:
                note_ids_with_comments.append(nid)

        if not note_ids_with_comments:
            logger.info(f"XHS {uid} 无需抓取评论（所有笔记评论数为0）")
            return

        # 不再限制笔记数量
        logger.info(f"XHS {uid} 开始抓取 {len(note_ids_with_comments)} 条笔记的评论")

        for note_id in note_ids_with_comments:
            try:
                comments = await self._intercept_note_comments(page, note_id)
                if comments:
                    await self._save_comments(note_id, comments)
                    logger.info(f"XHS 笔记 {note_id} 保存 {len(comments)} 条评论")
                await asyncio.sleep(1.5)
            except Exception as e:
                logger.error(f"XHS 抓取笔记 {note_id} 评论失败: {type(e).__name__}: {e}")

    async def _intercept_note_comments(self, page, note_id: str) -> list[dict]:
        """导航到笔记详情页，拦截评论API获取评论列表"""
        captured_comments = []
        seen_ids = set()

        async def on_comment_response(response):
            url = response.url
            is_comment_api = any(kw in url for kw in (
                '/api/sns/web/v2/comment/page',
                '/api/sns/web/v1/comment/page',
                'comment/page',
            ))
            if is_comment_api and response.status == 200:
                try:
                    body = await response.json()
                    data = body.get("data", {})
                    comments = data.get("comments", [])
                    for c in comments:
                        cid = c.get("id", "")
                        if cid and cid not in seen_ids:
                            seen_ids.add(cid)
                            captured_comments.append(c)
                            # 也收集子评论
                            sub_comments = c.get("sub_comments", [])
                            for sc in sub_comments:
                                scid = sc.get("id", "")
                                if scid and scid not in seen_ids:
                                    seen_ids.add(scid)
                                    captured_comments.append({
                                        **sc,
                                        "_parent_comment_id": cid,
                                    })
                    logger.debug(f"XHS 拦截到 {len(comments)} 条评论 (累计 {len(captured_comments)})")
                except Exception as e:
                    logger.debug(f"XHS 解析评论响应失败: {e}")

        page.on('response', on_comment_response)
        try:
            explore_url = f"https://www.xiaohongshu.com/explore/{note_id}"
            try:
                await page.goto(explore_url, wait_until="networkidle", timeout=15000)
            except Exception:
                try:
                    await page.goto(explore_url, wait_until="domcontentloaded", timeout=10000)
                except Exception:
                    pass
            await asyncio.sleep(3)

            # 尝试展开更多评论（点击"查看更多评论"）
            try:
                more_btns = page.locator('text=查看更多评论, text=展开更多, text=查看全部, [class*="show-more"], [class*="more-comment"]')
                for i in range(min(await more_btns.count(), 3)):
                    await more_btns.nth(i).click()
                    await asyncio.sleep(2)
            except Exception:
                pass

            # 滚动几次加载更多评论
            for _ in range(3):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1.5)

            return captured_comments
        finally:
            page.remove_listener('response', on_comment_response)

    async def _save_comments(self, note_id_str: str, comments: list[dict]):
        """保存爬取的评论到数据库"""
        from app.database import async_session
        from app.models.xhs_post import XHSNote, XHSComment
        from app.services.media_service import media_service
        from sqlalchemy import select

        async with async_session() as db:
            # 查找笔记的DB ID
            result = await db.execute(select(XHSNote).where(XHSNote.note_id == note_id_str))
            note = result.scalar_one_or_none()
            if not note:
                logger.warning(f"XHS 评论保存失败：笔记 {note_id_str} 不在数据库中")
                return

            # 获取已有评论ID
            existing_result = await db.execute(
                select(XHSComment.comment_id).where(XHSComment.note_id == note.id)
            )
            existing_ids = set(r[0] for r in existing_result.all() if r[0])

            # comment_id -> db id 映射（用于关联回复）
            cid_to_dbid = {}
            existing_comments = await db.execute(
                select(XHSComment).where(XHSComment.note_id == note.id)
            )
            for ec in existing_comments.scalars().all():
                if ec.comment_id:
                    cid_to_dbid[ec.comment_id] = ec.id

            new_count = 0
            for c in comments:
                cid = c.get("id", "")
                if not cid or cid in existing_ids:
                    continue

                user_info = c.get("user_info") or c.get("userInfo") or {}
                author_uid = user_info.get("user_id", "") or user_info.get("userId", "")
                author_nickname = user_info.get("nickname", "") or user_info.get("nickName", "")
                author_avatar_url = user_info.get("image", "") or user_info.get("avatar", "")

                # 下载评论者头像
                avatar_path = None
                if author_avatar_url:
                    avatar_path = await media_service.download_image(author_avatar_url, "avatar")

                # 解析时间
                comment_time = None
                create_time = c.get("create_time") or c.get("createTime")
                if create_time:
                    try:
                        comment_time = datetime.fromtimestamp(int(create_time) / 1000)
                    except Exception:
                        try:
                            comment_time = datetime.fromtimestamp(int(create_time))
                        except Exception:
                            pass

                # 解析回复目标
                parent_cid = c.get("_parent_comment_id", "")
                reply_to_db_id = cid_to_dbid.get(parent_cid) if parent_cid else None

                target_nickname = ""
                target_comment = c.get("target_comment") or c.get("targetComment") or {}
                if target_comment and isinstance(target_comment, dict):
                    target_user = target_comment.get("user_info") or target_comment.get("userInfo") or {}
                    target_nickname = target_user.get("nickname", "") or target_user.get("nickName", "") if isinstance(target_user, dict) else ""

                like_count = 0
                try:
                    like_count = int(c.get("like_count") or c.get("likeCount") or 0)
                except (ValueError, TypeError):
                    pass

                sub_comment_count = 0
                try:
                    sub_comment_count = int(c.get("sub_comment_count") or c.get("subCommentCount") or 0)
                except (ValueError, TypeError):
                    pass

                # IP属地
                comment_ip = c.get("ip_location", "") or c.get("ipLocation", "")

                # 是否作者
                show_tags = c.get("show_tags") or []
                is_author_flag = 1 if "is_author" in show_tags else 0

                comment = XHSComment(
                    note_id=note.id,
                    comment_id=cid,
                    author_uid=author_uid,
                    author_nickname=author_nickname,
                    author_avatar=avatar_path,
                    content=c.get("content", ""),
                    like_count=like_count,
                    comment_time=comment_time,
                    reply_to_id=reply_to_db_id,
                    target_nickname=target_nickname,
                    sub_comment_count=sub_comment_count,
                    ip_location=comment_ip,
                    is_author=is_author_flag,
                )
                db.add(comment)
                new_count += 1

                # flush to get ID for reply mapping
                await db.flush()
                cid_to_dbid[cid] = comment.id
                existing_ids.add(cid)

            await db.commit()
            if new_count:
                logger.info(f"XHS 笔记 {note_id_str} 新增 {new_count} 条评论")

    async def reset_browser_data(self) -> dict:
        """手动重置浏览器指纹和cookie，清除被风控标记的数据"""
        # 先关闭活动的浏览器
        await self._cleanup_browser()
        
        cleaned = []
        for subdir in ["xhs_login", "xhs_crawl"]:
            browser_data_dir = os.path.join(
                os.path.dirname(os.path.abspath(settings.static_dir)),
                "browser_data", subdir
            )
            if os.path.exists(browser_data_dir):
                shutil.rmtree(browser_data_dir, ignore_errors=True)
                cleaned.append(subdir)
                logger.info(f"已清理浏览器数据: {browser_data_dir}")
        
        self.login_status = "unknown"
        self.login_status_detail = "浏览器已重置"
        return {"ok": True, "cleaned": cleaned, "message": f"已清理: {', '.join(cleaned) if cleaned else '无需清理'}"}

    async def _cleanup_browser(self):
        """安全清理浏览器资源"""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            self._poll_task = None
        if self._browser_context:
            try:
                # 兼容两种模式：普通模式有browser，持久化模式只有context
                ctx = self._browser_context.get("browser") or self._browser_context.get("context")
                if ctx:
                    await ctx.close()
            except Exception:
                pass
            try:
                await self._browser_context["playwright"].stop()
            except Exception:
                pass
            self._browser_context = None
            # 浏览器关闭后，如果不是已登录成功则重置状态（防止异常中断后的旧状态误报）
            if self.login_status not in ("logged_in",):
                self.login_status = "unknown"
                self.login_status_detail = ""

    async def get_login_qrcode(self) -> Optional[str]:
        """通过Playwright获取小红书登录二维码"""
        await self._cleanup_browser()
        self.login_status = "getting_qrcode"
        self.login_status_detail = "正在初始化浏览器..."
        await self._update_login_debug_snapshot("开始获取小红书二维码")

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("Playwright未安装。请运行: pip install playwright && playwright install chromium")
            self.login_status = "error"
            self.login_status_detail = "Playwright未安装"
            await self._update_login_debug_snapshot("Playwright未安装")
            return None

        try:
            p = await async_playwright().start()
        except Exception as e:
            logger.error(f"Playwright启动失败: {type(e).__name__}: {e}", exc_info=True)
            self.login_status = "error"
            self.login_status_detail = f"Playwright启动失败: {e}"
            await self._update_login_debug_snapshot("Playwright启动失败")
            return None

        try:
            # 复用浏览器数据目录（维持设备指纹一致性，减少风控风险）
            # 如需重置，可通过前端"重置浏览器"按钮手动清理
            browser_data_dir = os.path.join(
                os.path.dirname(os.path.abspath(settings.static_dir)),
                "browser_data", "xhs_login"
            )
            os.makedirs(browser_data_dir, exist_ok=True)
            logger.info(f"复用XHS浏览器数据目录: {browser_data_dir}")

            context = await p.chromium.launch_persistent_context(
                browser_data_dir,
                headless=True,
                args=[
                    '--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-infobars',
                    '--window-size=1280,800',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--disable-site-isolation-trials',
                    '--disable-web-security',
                    '--disable-features=CrossSiteDocumentBlockingIfIsolating',
                    '--no-first-run',
                    '--no-default-browser-check',
                    '--disable-extensions',
                    '--disable-component-extensions-with-background-pages',
                    '--disable-default-apps',
                    '--disable-hang-monitor',
                    '--disable-prompt-on-repost',
                    '--disable-background-timer-throttling',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-renderer-backgrounding',
                    '--enable-features=NetworkService,NetworkServiceInProcess',
                ],
                viewport={"width": 1280, "height": 800},
                device_scale_factor=1,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                ignore_https_errors=True,
                extra_http_headers={
                    'Sec-CH-UA': '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
                    'Sec-CH-UA-Mobile': '?0',
                    'Sec-CH-UA-Platform': '"Windows"',
                },
            )
        except Exception as e:
            logger.error(f"Chromium启动失败（可能缺少系统依赖）: {type(e).__name__}: {e}", exc_info=True)
            self.login_status = "error"
            self.login_status_detail = f"Chromium启动失败: {e}"
            await self._update_login_debug_snapshot("Chromium启动失败")
            try:
                await p.stop()
            except Exception:
                pass
            return None

        try:
            page = context.pages[0] if context.pages else await context.new_page()

            # 清除旧cookie确保干净的登录状态
            await context.clear_cookies()

            # 注入全面的反检测脚本（隐藏Playwright/headless特征）
            await context.add_init_script("""
                // ===== 1. 隐藏 webdriver 标志 =====
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                delete Object.getPrototypeOf(navigator).webdriver;

                // ===== 2. 伪装 plugins (模拟真实Chrome插件) =====
                Object.defineProperty(navigator, 'plugins', {
                    get: () => {
                        const mk = (n, f, d, m) => {
                            const p = { name: n, filename: f, description: d, length: 1 };
                            p[0] = { type: m, suffixes: 'pdf', description: d, enabledPlugin: p };
                            return p;
                        };
                        const pl = [
                            mk('Chrome PDF Plugin', 'internal-pdf-viewer', 'Portable Document Format', 'application/x-google-chrome-pdf'),
                            mk('Chrome PDF Viewer', 'mhjfbmdgcfjbbpaeojofohoefgiehjai', '', 'application/pdf'),
                            mk('Native Client', 'internal-nacl-plugin', '', 'application/x-nacl'),
                        ];
                        pl.item = i => pl[i] || null;
                        pl.namedItem = n => pl.find(p => p.name === n) || null;
                        pl.refresh = () => {};
                        return pl;
                    },
                });

                // ===== 3. 伪装 mimeTypes =====
                Object.defineProperty(navigator, 'mimeTypes', {
                    get: () => {
                        const mt = [
                            { type: 'application/pdf', suffixes: 'pdf', description: '', enabledPlugin: { name: 'Chrome PDF Viewer' } },
                            { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format', enabledPlugin: { name: 'Chrome PDF Plugin' } },
                        ];
                        mt.item = i => mt[i] || null;
                        mt.namedItem = n => mt.find(m => m.type === n) || null;
                        return mt;
                    },
                });

                // ===== 4. 伪装 languages =====
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });

                // ===== 5. window.chrome 完整对象 =====
                window.chrome = {
                    app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } },
                    runtime: {
                        OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
                        OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
                        PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
                        PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
                        PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
                        RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' },
                        connect: function() { return { onDisconnect: { addListener: function() {} } }; },
                        id: undefined
                    },
                    loadTimes: function() {
                        const t = Date.now() / 1000;
                        return { requestTime: t - 2, startLoadTime: t - 1.8, commitLoadTime: t - 1.5, finishDocumentLoadTime: t - 0.5, finishLoadTime: t - 0.2, firstPaintTime: t - 1.2, firstPaintAfterLoadTime: 0, navigationType: 'Other', wasFetchedViaSpdy: true, wasNpnNegotiated: true, npnNegotiatedProtocol: 'h2', wasAlternateProtocolAvailable: false, connectionInfo: 'h2' };
                    },
                    csi: function() { return { onloadT: Date.now(), startE: Date.now() - 2000, pageT: 2000, tran: 15 }; }
                };

                // ===== 6. Permissions API =====
                const origQuery = window.navigator.permissions?.query;
                if (origQuery) {
                    window.navigator.permissions.query = (p) => (
                        p.name === 'notifications' ? Promise.resolve({ state: Notification.permission }) : origQuery(p)
                    );
                }

                // ===== 7. WebGL 渲染器伪装 (模拟真实显卡) =====
                const getParam = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(p) {
                    if (p === 37445) return 'Google Inc. (NVIDIA)';
                    if (p === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)';
                    return getParam.call(this, p);
                };
                if (window.WebGL2RenderingContext) {
                    const getParam2 = WebGL2RenderingContext.prototype.getParameter;
                    WebGL2RenderingContext.prototype.getParameter = function(p) {
                        if (p === 37445) return 'Google Inc. (NVIDIA)';
                        if (p === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)';
                        return getParam2.call(this, p);
                    };
                }

                // ===== 8. navigator.connection =====
                if (!navigator.connection) {
                    Object.defineProperty(navigator, 'connection', {
                        get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false, onchange: null })
                    });
                }

                // ===== 9. Client Hints API (navigator.userAgentData) =====
                Object.defineProperty(navigator, 'userAgentData', {
                    get: () => ({
                        brands: [
                            { brand: 'Chromium', version: '136' },
                            { brand: 'Google Chrome', version: '136' },
                            { brand: 'Not.A/Brand', version: '99' }
                        ],
                        mobile: false,
                        platform: 'Windows',
                        getHighEntropyValues: () => Promise.resolve({
                            brands: [{ brand: 'Chromium', version: '136' }, { brand: 'Google Chrome', version: '136' }, { brand: 'Not.A/Brand', version: '99' }],
                            mobile: false, platform: 'Windows', platformVersion: '15.0.0',
                            architecture: 'x86', bitness: '64', model: '',
                            uaFullVersion: '136.0.7103.93',
                            fullVersionList: [{ brand: 'Chromium', version: '136.0.7103.93' }, { brand: 'Google Chrome', version: '136.0.7103.93' }, { brand: 'Not.A/Brand', version: '99.0.0.0' }]
                        })
                    })
                });

                // ===== 10. Notification =====
                if (typeof Notification !== 'undefined') {
                    Object.defineProperty(Notification, 'permission', { get: () => 'default' });
                }

                // ===== 11. window 尺寸 (headless模式outerWidth/outerHeight=0) =====
                if (window.outerWidth === 0) Object.defineProperty(window, 'outerWidth', { get: () => 1280 });
                if (window.outerHeight === 0) Object.defineProperty(window, 'outerHeight', { get: () => 885 });
                // screenX / screenY
                if (window.screenX === 0 && window.screenY === 0) {
                    Object.defineProperty(window, 'screenX', { get: () => 20 });
                    Object.defineProperty(window, 'screenY', { get: () => 20 });
                }

                // ===== 12. 屏幕属性 =====
                Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
                Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });
                Object.defineProperty(screen, 'width', { get: () => 1920 });
                Object.defineProperty(screen, 'height', { get: () => 1080 });
                Object.defineProperty(screen, 'availWidth', { get: () => 1920 });
                Object.defineProperty(screen, 'availHeight', { get: () => 1040 });

                // ===== 13. navigator.hardwareConcurrency / deviceMemory / platform =====
                Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
                Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
                Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
                Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
                Object.defineProperty(navigator, 'vendor', { get: () => 'Google Inc.' });

                // ===== 14. document.hasFocus() (headless总是false) =====
                Document.prototype.hasFocus = function() { return true; };
                Object.defineProperty(document, 'hidden', { get: () => false });
                Object.defineProperty(document, 'visibilityState', { get: () => 'visible' });

                // ===== 15. CDP / 自动化框架检测绕过 =====
                // 删除常见的自动化痕迹属性
                const automationProps = [
                    '__webdriver_evaluate', '__selenium_evaluate', '__fxdriver_evaluate',
                    '__driver_evaluate', '__webdriver_unwrap', '__driver_unwrap',
                    '__selenium_unwrap', '__fxdriver_unwrap', '__lastWatirAlert',
                    '__lastWatirConfirm', '__lastWatirPrompt', '_Selenium_IDE_Recorder',
                    '_selenium', 'calledSelenium', '_WEBDRIVER_ELEM_CACHE',
                    'ChromeDriverw', 'driver-hierarchylevel', '__playwright_evaluation_script__',
                    '__pw_manual',
                ];
                for (const prop of automationProps) {
                    delete window[prop];
                    delete document[prop];
                }

                // ===== 16. 伪装 Intl.DateTimeFormat (时区一致性) =====
                const origDTF = Intl.DateTimeFormat;
                const dtfProxy = new Proxy(origDTF, {
                    construct(target, args) {
                        if (!args[1] || !args[1].timeZone) {
                            args[1] = args[1] || {};
                            args[1].timeZone = 'Asia/Shanghai';
                        }
                        return new target(...args);
                    }
                });
                Intl.DateTimeFormat = dtfProxy;

                // ===== 17. Performance API (headless缺少memory) =====
                if (!performance.memory) {
                    Object.defineProperty(performance, 'memory', {
                        get: () => ({
                            jsHeapSizeLimit: 2172649472,
                            totalJSHeapSize: 35839484,
                            usedJSHeapSize: 24190288,
                        })
                    });
                }

                // ===== 18. 阻止 iframe contentWindow 检测 =====
                const origCreateElement = document.createElement.bind(document);
                document.createElement = function(tag, opts) {
                    const el = origCreateElement(tag, opts);
                    if (tag.toLowerCase() === 'iframe') {
                        const origGet = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow').get;
                        Object.defineProperty(el, 'contentWindow', {
                            get: function() {
                                const w = origGet.call(this);
                                if (w) {
                                    try {
                                        Object.defineProperty(w.navigator, 'webdriver', { get: () => undefined });
                                    } catch(e) {}
                                }
                                return w;
                            }
                        });
                    }
                    return el;
                };

                // ===== 19. 伪装 speechSynthesis (headless可能缺失) =====
                if (!window.speechSynthesis) {
                    window.speechSynthesis = {
                        getVoices: () => [],
                        speak: () => {},
                        cancel: () => {},
                        pause: () => {},
                        resume: () => {},
                        onvoiceschanged: null,
                        paused: false,
                        pending: false,
                        speaking: false,
                    };
                }

                // ===== 20. canvas fingerprint 轻微噪声 (防止检测到完全一致的canvas指纹) =====
                const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
                HTMLCanvasElement.prototype.toDataURL = function(type) {
                    if (this.width > 16 && this.height > 16) {
                        const ctx = this.getContext('2d');
                        if (ctx) {
                            const style = ctx.fillStyle;
                            ctx.fillStyle = 'rgba(255,255,255,0.01)';
                            ctx.fillRect(0, 0, 1, 1);
                            ctx.fillStyle = style;
                        }
                    }
                    return origToDataURL.apply(this, arguments);
                };
            """)

            logger.debug("已注入全面的反检测脚本")

            # 阻止字体加载
            async def _abort_route(route):
                await route.abort()
            await page.route(re.compile(r"\.(woff2?|ttf|otf|eot)(\?|$)", re.IGNORECASE), _abort_route)

            try:
                await page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded", timeout=20000)
            except Exception:
                logger.warning("小红书页面加载超时，尝试继续...")
            await asyncio.sleep(3)

            # 尝试多种方式触发登录弹窗
            login_btn = page.locator('text=登录, text=Log in, .login-btn, [class*="login"]')
            if await login_btn.count() > 0:
                await login_btn.first.click()
                await asyncio.sleep(2)

            # 尝试切换到二维码登录模式
            try:
                qr_tab = page.locator('text=扫码登录, text=二维码, [class*="qrcode-tab"]')
                if await qr_tab.count() > 0:
                    await qr_tab.first.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

            qrcode_b64 = None
            qr_selectors = [
                '[class*="qrcode"] canvas',
                '[class*="QRCode"] canvas',
                '[class*="qr"] canvas',
                '[class*="qrcode"] img',
                '[class*="QRCode"] img',
                '[class*="qr"] img',
                '.login-container canvas',
                '.login-container img[src^="data:image"]',
                'img[src*="qrcode"]',
            ]

            for selector in qr_selectors:
                try:
                    locator = page.locator(selector)
                    count = await locator.count()
                    if count == 0:
                        continue

                    for idx in range(min(count, 4)):
                        candidate = locator.nth(idx)
                        if not await candidate.is_visible():
                            continue
                        box = await candidate.bounding_box()
                        if not box:
                            continue
                        width = box.get("width", 0)
                        height = box.get("height", 0)
                        if width < 120 or height < 120:
                            continue
                        if abs(width - height) > 80:
                            continue

                        screenshot = await candidate.screenshot(timeout=10000)
                        qrcode_b64 = base64.b64encode(screenshot).decode()
                        logger.info(f"小红书二维码元素截图成功: selector={selector}, size={int(width)}x{int(height)}")
                        break

                    if qrcode_b64:
                        break
                except Exception as e_scr:
                    logger.debug(f"小红书二维码候选截图失败 selector={selector}: {e_scr}")

            if not qrcode_b64:
                # 最终兜底：如果页面上有登录弹窗但未找到具体二维码元素，用全页面截图
                try:
                    login_modal = page.locator('[class*="login"], [class*="Login"], .modal, [role="dialog"]')
                    if await login_modal.count() > 0:
                        modal_box = await login_modal.first.bounding_box()
                        if modal_box and modal_box.get("width", 0) > 200:
                            screenshot = await login_modal.first.screenshot(timeout=10000)
                            qrcode_b64 = base64.b64encode(screenshot).decode()
                            logger.info("小红书：使用登录弹窗整体截图作为二维码兜底")
                except Exception as e_modal:
                    logger.debug(f"弹窗截图兜底失败: {e_modal}")

            if not qrcode_b64:
                # 极端兜底：全页面截图
                try:
                    screenshot = await page.screenshot(type="jpeg", quality=70, timeout=10000)
                    qrcode_b64 = base64.b64encode(screenshot).decode()
                    logger.info("小红书：使用全页面截图兜底")
                except Exception as e_full:
                    logger.error(f"全页面截图也失败: {e_full}")

            if not qrcode_b64:
                logger.error("未检测到有效的小红书二维码元素，所有截图方式均失败")
                self.login_status = "error"
                self.login_status_detail = "未检测到二维码，请稍后重试"
                await self._update_login_debug_snapshot("未检测到有效二维码元素")
                try:
                    await context.close()
                except Exception:
                    pass
                try:
                    await p.stop()
                except Exception:
                    pass
                return None

            self._browser_context = {"context": context, "page": page, "playwright": p}
            self.login_status = "waiting_scan"
            self.login_status_detail = "二维码已生成，等待扫码确认"
            await self._update_login_debug_snapshot("二维码已生成")
            self._poll_task = asyncio.create_task(self._poll_login_status())
            return qrcode_b64

        except Exception as e:
            logger.error(f"获取小红书二维码失败: {type(e).__name__}: {e}", exc_info=True)
            self.login_status = "error"
            self.login_status_detail = f"获取二维码失败: {e}"
            await self._update_login_debug_snapshot(f"获取二维码失败: {type(e).__name__}")
            try:
                await context.close()
            except Exception:
                pass
            try:
                await p.stop()
            except Exception:
                pass
            return None

    async def _poll_login_status(self):
        """轮询小红书登录状态
        
        核心检测：每3秒检查context.cookies()是否出现登录标识cookie。
        辅助：网络响应拦截、WebSocket监控、主页面JS cookie检测。
        不开新标签页，不干扰浏览器页面和WS预览流。
        """
        if not self._browser_context:
            return

        page = self._browser_context["page"]
        context = self._browser_context["context"]
        consecutive_errors = 0

        # 记录初始cookie快照（用XHS域名限定）
        initial_xhs_cookies = {}
        try:
            for c in await context.cookies(["https://www.xiaohongshu.com", "https://edith.xiaohongshu.com"]):
                initial_xhs_cookies[c["name"]] = c["value"]
            logger.info(f"XHS初始cookie({len(initial_xhs_cookies)}): {sorted(initial_xhs_cookies.keys())}")
        except Exception:
            pass

        # 登录cookie标识集合（只有登录后才会出现）
        LOGIN_COOKIE_NAMES = {
            'web_session', 'access-token', 'access-token-v2',
            'customer-sso-sid', 'galaxy_creator_session_id',
        }

        # 网络响应监控
        login_network_data = None
        login_error_count = 0  # 跟踪"登录异常"次数

        async def _on_response(response):
            nonlocal login_network_data, login_error_count
            if login_network_data:
                return
            try:
                url = response.url
                if 'xiaohongshu' not in url or response.status != 200:
                    return
                ct = response.headers.get('content-type', '')
                if 'json' not in ct:
                    return
                # 排除验证码/captcha相关API（redcaptcha的status:2是"验证通过"不是"登录成功"）
                url_lower = url.lower()
                if 'redcaptcha' in url_lower or 'captcha' in url_lower:
                    return
                body = await response.text()
                if not body or len(body) > 100000:
                    return
                if any(k in url_lower for k in ['login', 'qr', 'check', 'token', 'activate', 'auth', 'sns']):
                    logger.info(f"XHS-API: {url.split('?')[0][-80:]} body={body[:200]}")
                try:
                    data = json.loads(body)
                    # 检测"登录异常"响应
                    if isinstance(data, dict) and data.get("code") == -1 and data.get("msg") == "登录异常":
                        login_error_count += 1
                    elif isinstance(data, dict) and data.get("code") == 0:
                        rd = data.get("data", {})
                        if isinstance(rd, dict):
                            # 过滤 guest:true 的 /user/me 响应（不是真正登录）
                            if rd.get("guest") is True:
                                logger.debug(f"XHS网络拦截: 跳过guest响应")
                            else:
                                has_token = bool(rd.get("token") or rd.get("access_token") or rd.get("sid"))
                                has_session = bool(rd.get("session") or rd.get("secure_session"))
                                has_uid = bool(rd.get("user_id") or rd.get("userId") or rd.get("uid"))
                                # 不再将qr status:2当作登录成功（那是captcha验证通过）
                                qr_st = str(rd.get("login_status", ""))
                                if has_token or (has_uid and has_session) or qr_st in ('success', 'confirmed'):
                                    login_network_data = rd
                                    logger.info(f"XHS网络拦截: 登录成功! keys={list(rd.keys())[:8]}")
                except (json.JSONDecodeError, ValueError):
                    pass
            except Exception:
                pass

        page.on("response", _on_response)

        # WebSocket监控
        try:
            def _on_ws_created(ws):
                nonlocal login_network_data
                logger.info(f"XHS WebSocket连接: {(ws.url or '')[:100]}")
                def _on_ws_frame(payload):
                    nonlocal login_network_data
                    try:
                        msg = payload if isinstance(payload, str) else str(payload)
                        if len(msg) > 10000:
                            return
                        if any(k in msg.lower() for k in ['login', 'success', 'confirm', 'token', '"code":0']):
                            logger.info(f"XHS WS消息: {msg[:300]}")
                            if not login_network_data:
                                try:
                                    data = json.loads(msg)
                                    if isinstance(data, dict):
                                        rd = data.get('data', data)
                                        if isinstance(rd, dict) and (rd.get('token') or rd.get('user_id') or rd.get('userId')):
                                            login_network_data = rd
                                            logger.info(f"XHS WS登录成功信号!")
                                except (json.JSONDecodeError, ValueError):
                                    pass
                    except Exception:
                        pass
                ws.on("framereceived", _on_ws_frame)
            page.on("websocket", _on_ws_created)
        except Exception:
            pass

        last_cookie_count = len(initial_xhs_cookies)

        for i in range(60):  # 最多轮询3分钟
            await asyncio.sleep(3)
            try:
                if not self._browser_context:
                    return

                # 检测"登录异常"——浏览器数据被风控，需要重置
                if login_error_count >= 5:
                    logger.warning(f"XHS检测到连续{login_error_count}次'登录异常'，浏览器指纹可能被风控，自动重置浏览器数据")
                    self.login_status = "error"
                    self.login_status_detail = "检测到登录异常（浏览器被风控），已自动重置，请重新获取二维码"
                    await self._update_login_debug_snapshot("登录异常，自动重置浏览器数据")
                    # 关闭当前浏览器并清理数据
                    await self._cleanup_browser()
                    browser_data_dir = os.path.join(
                        os.path.dirname(os.path.abspath(settings.static_dir)),
                        "browser_data", "xhs_login"
                    )
                    if os.path.exists(browser_data_dir):
                        shutil.rmtree(browser_data_dir, ignore_errors=True)
                        logger.info(f"已自动清理被风控的浏览器数据: {browser_data_dir}")
                    return

                consecutive_errors = 0
                verified = False
                xhs_user_id = ""
                nickname = ""
                red_id = ""

                # ===== 核心方法: context.cookies() 轮询 =====
                # 不开新页面，不干扰浏览器，纯cookie检测
                try:
                    xhs_cookies = await context.cookies(
                        ["https://www.xiaohongshu.com", "https://edith.xiaohongshu.com"]
                    )
                    cookie_dict = {c["name"]: c["value"] for c in xhs_cookies}
                    cookie_names = set(cookie_dict.keys())

                    # 检测登录cookie
                    found_login_cookies = cookie_names & LOGIN_COOKIE_NAMES
                    new_login_cookies = found_login_cookies - set(initial_xhs_cookies.keys())

                    # 检查cookie值是否变化（同名但值不同也算新登录）
                    value_changed_cookies = set()
                    for cn in found_login_cookies:
                        if cn in initial_xhs_cookies and cookie_dict.get(cn) != initial_xhs_cookies[cn]:
                            value_changed_cookies.add(cn)

                    if new_login_cookies or value_changed_cookies:
                        changed = new_login_cookies | value_changed_cookies
                        logger.info(f"XHS轮询#{i+1}: 发现登录cookie变化! 新增={sorted(new_login_cookies)} 值变={sorted(value_changed_cookies)}")
                        verified = True
                        # 不设置xhs_user_id为占位符，留空等后续/user/me API获取真实ID
                    elif found_login_cookies and not initial_xhs_cookies:
                        # 初始没有cookie（可能clear失败），但现在有了
                        logger.info(f"XHS轮询#{i+1}: 登录cookie出现（初始为空）: {sorted(found_login_cookies)}")
                        verified = True

                    # 每6次（18秒）记录一次cookie状态
                    if i % 6 == 0:
                        current_count = len(xhs_cookies)
                        if abs(current_count - last_cookie_count) >= 2:
                            logger.info(f"XHS cookie变化: {last_cookie_count}→{current_count}, names={sorted(cookie_names)}")
                            last_cookie_count = current_count
                except Exception as e_cookie:
                    logger.debug(f"XHS cookie检查异常: {e_cookie}")

                # ===== 辅助: 网络拦截信号 =====
                if not verified and login_network_data:
                    net_uid = str(login_network_data.get("user_id") or login_network_data.get("userId") or "")
                    if net_uid:
                        xhs_user_id = net_uid
                    nickname = str(login_network_data.get("nickname") or "")
                    verified = True
                    logger.info(f"XHS网络信号: uid={xhs_user_id}")

                # ===== 辅助: 主页面JS cookie检测（每15秒） =====
                if not verified and i > 0 and i % 5 == 0:
                    try:
                        js_cookie = await page.evaluate("() => (document.cookie || '').substring(0, 600)")
                        if isinstance(js_cookie, str) and any(k in js_cookie for k in LOGIN_COOKIE_NAMES):
                            logger.info(f"XHS主页面JS cookie中发现登录标识!")
                            verified = True
                    except Exception:
                        pass

                if not verified:
                    self.login_status = "waiting_scan"
                    self.login_status_detail = "等待扫码"
                    if i % 3 == 0:
                        try:
                            cnt = len(await context.cookies(
                                ["https://www.xiaohongshu.com", "https://edith.xiaohongshu.com"]
                            ))
                            info = f"cookies={cnt}"
                        except Exception:
                            info = "检查中"
                        await self._update_login_debug_snapshot(f"等待扫码（{info}）")
                    continue

                # ===== 验证通过，真正登录成功 =====
                # 如果cookie轮询检测到登录但user_id为空，先从login_network_data获取
                if not xhs_user_id and login_network_data:
                    net_uid = str(login_network_data.get("user_id") or login_network_data.get("userId") or "")
                    if net_uid and net_uid != "undefined":
                        xhs_user_id = net_uid
                        logger.info(f"XHS登录: 从网络拦截数据获取user_id={xhs_user_id}")
                    net_nick = str(login_network_data.get("nickname") or "")
                    if net_nick and not nickname:
                        nickname = net_nick

                # 先导航到首页让cookie通过Set-Cookie头正确写入浏览器
                try:
                    await page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded", timeout=15000)
                    logger.info("XHS登录后导航到首页，等待cookie传播...")
                except Exception as e_nav:
                    logger.debug(f"XHS登录后导航首页异常（非致命）: {e_nav}")
                await asyncio.sleep(5)  # 等待更长时间确保cookie传播
                # 获取XHS域名的cookies用于保存（带域名过滤更可靠）
                cookies = await context.cookies(
                    ["https://www.xiaohongshu.com", "https://edith.xiaohongshu.com"]
                )
                if len(cookies) < 5:
                    # cookie太少，可能还没传播完，再等几秒重试
                    logger.warning(f"XHS登录后cookie仅{len(cookies)}个，等待更多cookie传播...")
                    await asyncio.sleep(5)
                    cookies = await context.cookies(
                        ["https://www.xiaohongshu.com", "https://edith.xiaohongshu.com"]
                    )
                logger.info(f"小红书登录验证成功: {nickname} (ID: {xhs_user_id}), cookies={len(cookies)}")

                # 尝试获取更精确的用户信息
                from app.database import async_session
                from app.models.account import Account
                from app.services.media_service import media_service
                from sqlalchemy import select

                login_avatar_local = None
                login_red_id = ""
                my_avatar_url = ""

                # ===== 方法1: 调用 /api/sns/web/v2/user/me 获取当前登录用户信息（最可靠） =====
                # 带重试：登录后session可能需要几秒才能生效
                me_result = None
                for _retry in range(3):
                    try:
                        me_result = await page.evaluate("""async () => {
                            try {
                                const resp = await fetch('https://edith.xiaohongshu.com/api/sns/web/v2/user/me', {
                                    method: 'GET',
                                    credentials: 'include',
                                    headers: {
                                        'Accept': 'application/json, text/plain, */*',
                                        'Origin': 'https://www.xiaohongshu.com',
                                        'Referer': 'https://www.xiaohongshu.com/',
                                    }
                                });
                                if (!resp.ok) return {error: 'HTTP ' + resp.status};
                                const json = await resp.json();
                                if (json.code === 0 && json.data) {
                                    return {
                                        ok: true,
                                        red_id: json.data.red_id || '',
                                        user_id: json.data.user_id || '',
                                        nickname: json.data.nickname || '',
                                        desc: json.data.desc || '',
                                        imageb: json.data.imageb || '',
                                        images: json.data.images || '',
                                    };
                                }
                                return {error: 'code=' + json.code};
                            } catch(e) { return {error: e.message}; }
                        }""")
                        if isinstance(me_result, dict) and me_result.get("ok"):
                            break
                        logger.debug(f"XHS /user/me 第{_retry+1}次尝试失败: {me_result}, 等待重试...")
                        await asyncio.sleep(3)
                    except Exception as e_retry:
                        logger.debug(f"XHS /user/me 第{_retry+1}次异常: {e_retry}")
                        await asyncio.sleep(3)

                if isinstance(me_result, dict) and me_result.get("ok"):
                    if me_result.get("red_id"):
                        login_red_id = str(me_result["red_id"]).strip()
                        logger.info(f"XHS登录: /user/me API获取红薯号={login_red_id}")
                    if me_result.get("nickname"):
                        nickname = me_result["nickname"]
                        logger.info(f"XHS登录: /user/me API获取昵称={nickname}")
                    if me_result.get("user_id") and me_result["user_id"] != xhs_user_id:
                        logger.info(f"XHS登录: /user/me API修正user_id {xhs_user_id} → {me_result['user_id']}")
                        xhs_user_id = me_result["user_id"]
                    my_avatar_url = me_result.get("imageb") or me_result.get("images") or ""
                else:
                    logger.debug(f"XHS /user/me API调用失败: {me_result}")

                # ===== 方法2: 新标签页访问profile，从DOM解析红薯号/昵称/头像 =====
                # 注意: 必须用新标签页，不能在主页面(QR码页面)上导航
                if not login_red_id or not my_avatar_url:
                    info_page = None
                    try:
                        if xhs_user_id and re.match(r'^[a-f0-9]{24}$', xhs_user_id):
                            profile_url = f"https://www.xiaohongshu.com/user/profile/{xhs_user_id}"
                            info_page = await context.new_page()
                            await info_page.goto(profile_url, wait_until="domcontentloaded", timeout=15000)
                            await asyncio.sleep(3)

                            # 从DOM元素解析（最直观可靠）
                            dom_info = await info_page.evaluate("""() => {
                                const info = {};
                                // 红薯号: <span class="user-redId">小红书号：26433848594</span>
                                const redIdEl = document.querySelector('.user-redId, [class*="user-redId"]');
                                if (redIdEl) {
                                    const text = redIdEl.textContent || '';
                                    const m = text.match(/[：:]\s*(\S+)/);
                                    if (m) info.red_id = m[1];
                                }
                                // 昵称: <div class="user-name">天下第一伤心男子</div>
                                const nameEl = document.querySelector('.user-name, [class*="user-name"]');
                                if (nameEl) {
                                    info.nickname = (nameEl.textContent || '').trim();
                                }
                                // 头像: <img class="user-image" src="...">
                                const avatarEl = document.querySelector('.user-image, [class*="user-image"]');
                                if (avatarEl) {
                                    let src = avatarEl.getAttribute('src') || '';
                                    // 去掉模糊参数（blur）
                                    src = src.split('|')[0];
                                    info.avatar = src;
                                }
                                // 签名
                                const descEl = document.querySelector('.user-desc, [class*="user-desc"]');
                                if (descEl) {
                                    info.desc = (descEl.textContent || '').trim();
                                }
                                return info;
                            }""")
                            if dom_info.get("red_id") and not login_red_id:
                                login_red_id = str(dom_info["red_id"]).strip()
                                logger.info(f"XHS登录: DOM解析获取红薯号={login_red_id}")
                            if dom_info.get("nickname") and not nickname:
                                nickname = dom_info["nickname"]
                                logger.info(f"XHS登录: DOM解析获取昵称={nickname}")
                            if dom_info.get("avatar") and not my_avatar_url:
                                my_avatar_url = dom_info["avatar"]
                                logger.info(f"XHS登录: DOM解析获取头像URL")

                            # 方法3兜底: __INITIAL_STATE__
                            if not login_red_id:
                                state_info = await info_page.evaluate("""() => {
                                    try {
                                        const state = window.__INITIAL_STATE__;
                                        if (!state) return {};
                                        let s = JSON.stringify(state).replace(/undefined/g, 'null');
                                        const parsed = JSON.parse(s);
                                        const info = {};
                                        const userPage = parsed.user?.userPageData;
                                        if (userPage) {
                                            info.red_id = userPage.basicInfo?.redId || '';
                                            info.nickname = userPage.basicInfo?.nickname || '';
                                            info.avatar = userPage.basicInfo?.imageb || userPage.basicInfo?.image || '';
                                        }
                                        if (!info.red_id) {
                                            const m = s.match(/"redId"\\s*:\\s*"([^"]+)"/);
                                            if (m) info.red_id = m[1];
                                        }
                                        return info;
                                    } catch(e) { return {}; }
                                }""")
                                if state_info.get("red_id"):
                                    login_red_id = str(state_info["red_id"]).strip()
                                    logger.info(f"XHS登录: __INITIAL_STATE__获取红薯号={login_red_id}")
                                if state_info.get("nickname") and not nickname:
                                    nickname = state_info["nickname"]
                                if state_info.get("avatar") and not my_avatar_url:
                                    my_avatar_url = state_info["avatar"]
                    except Exception as e_profile:
                        logger.debug(f"XHS登录: Profile信息获取失败（非致命）: {e_profile}")
                    finally:
                        if info_page:
                            try:
                                await info_page.close()
                            except Exception:
                                pass

                # 下载头像到本地
                if my_avatar_url:
                    try:
                        login_avatar_local = await media_service.download_image(my_avatar_url, "avatar")
                        if login_avatar_local:
                            logger.info(f"XHS登录: 头像已下载到 {login_avatar_local}")
                    except Exception as e_av:
                        logger.debug(f"XHS登录获取头像失败（非致命）: {e_av}")

                # 最终使用之前通过DOM获取的red_id作为兜底
                if not login_red_id and red_id:
                    login_red_id = red_id

                async with async_session() as db:
                    # 查找是否已存在相同platform_uid的账号
                    existing_account = None
                    if xhs_user_id:
                        result = await db.execute(
                            select(Account).where(
                                Account.platform == "xhs",
                                Account.is_target == 0,
                                Account.platform_uid == xhs_user_id,
                            )
                        )
                        existing_account = result.scalars().first()

                    from app.services.account_risk_service import mark_account_verified

                    if existing_account:
                        # 同一用户重新登录 → 更新
                        account = existing_account
                        account.cookies = json.dumps(cookies)
                        account.last_login = datetime.now()
                        if login_red_id:
                            account.account_id = login_red_id
                        if nickname:
                            account.nickname = nickname
                        if login_avatar_local:
                            if account.avatar_url and account.avatar_url != login_avatar_local:
                                history = account.avatar_history or []
                                history.append({"url": account.avatar_url, "time": datetime.now().isoformat()})
                                account.avatar_history = history
                            account.avatar_url = login_avatar_local
                        mark_account_verified(account)
                        logger.info(f"XHS登录: 更新已有账号 id={account.id}, uid={xhs_user_id}")
                    else:
                        # 没有匹配的平台UID → 新增账号（不覆盖其他账号）
                        account = Account(
                            platform="xhs",
                            account_id=login_red_id or xhs_user_id or "xhs_login",
                            platform_uid=xhs_user_id,
                            nickname=nickname,
                            avatar_url=login_avatar_local or "",
                            cookies=json.dumps(cookies),
                            is_target=0,
                            last_login=datetime.now(),
                        )
                        mark_account_verified(account)
                        db.add(account)
                        logger.info(f"XHS登录: 新增账号 uid={xhs_user_id}, nickname={nickname}")
                    await db.commit()

                self.login_status = "logged_in"
                self.login_status_detail = "登录成功，Cookie已保存"
                await self._update_login_debug_snapshot(f"登录成功: uid={xhs_user_id}")
                break
            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    logger.warning(f"小红书登录轮询连续{consecutive_errors}次异常，停止: {e}")
                    if self.login_status == "waiting_scan":
                        self.login_status = "expired"
                        self.login_status_detail = f"轮询异常: {e}"
                        await self._update_login_debug_snapshot("轮询异常达到上限，终止")
                    break
        else:
            if self.login_status == "waiting_scan":
                self.login_status = "expired"
                self.login_status_detail = "登录超时（3分钟未完成）"
                logger.info("小红书登录二维码已过期（3分钟未扫描）")
                await self._update_login_debug_snapshot("登录超时")

        # 清理网络响应监听器
        try:
            page.remove_listener("response", _on_response)
        except Exception:
            pass
        try:
            page.remove_listener("websocket", _on_ws_created)
        except Exception:
            pass

        # 登录成功时，等待预览窗口断开后再关闭浏览器
        if self.login_status == "logged_in":
            from app.routers.ws import active_preview_platforms
            logger.info("小红书登录成功，等待预览窗口断开后关闭浏览器（最长5分钟）")
            for _ in range(60):  # 最长5分钟
                await asyncio.sleep(5)
                if "xhs" not in active_preview_platforms:
                    logger.info("XHS预览窗口已断开，5秒后关闭浏览器")
                    await asyncio.sleep(5)
                    break
        await self._cleanup_browser()

    # Cookie过期通知频率限制（平台 -> 上次通知时间）
    _cookie_notify_timestamps: dict = {}

    async def _notify_cookie_expired(self, platform: str, detail: str = ""):
        """Cookie过期时通过NapCat通知管理员。
        跳过条件：
        - 通知功能未开启
        - 该平台的登录账号全部被手动禁用
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
                platform_key = "xhs" if "红书" in platform or "xhs" in platform.lower() else "qq"
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


xhs_crawler = XHSCrawler()

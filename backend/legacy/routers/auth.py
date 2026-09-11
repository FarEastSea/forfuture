from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from legacy.database import get_db
from legacy.models.account import Account
from legacy.config import settings as _settings
from legacy.security import require_admin_http
from legacy.services.account_risk_service import ACTIVE_STATUS, DISABLED_STATUS, RELOGIN_PENDING_STATUS, is_account_in_cooldown
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import asyncio
import os

router = APIRouter(dependencies=[Depends(require_admin_http)])


def _verify_avatar(avatar_url: str | None, platform: str = "", account_id: str = "") -> str:
    """如果avatar_url指向本地文件但文件不存在，回退到q.qlogo.cn CDN（QQ）或置空"""
    cdn_fallback = ""
    if platform == "qq" and account_id:
        cdn_fallback = f"https://q.qlogo.cn/headimg_dl?dst_uin={account_id}&spec=640&img_type=jpg"
    if not avatar_url:
        return cdn_fallback
    if avatar_url.startswith("/static/"):
        fp = os.path.join(_settings.static_dir, avatar_url[len("/static/"):])
        if os.path.isfile(fp):
            return avatar_url
        return cdn_fallback  # 文件不存在，回退CDN
    return avatar_url


def _update_nickname(account, new_nickname: str):
    """更新账号昵称，有变化时记录到昵称历史"""
    if not new_nickname or new_nickname == account.nickname:
        return
    old = account.nickname
    if old and old != new_nickname:
        history = account.nickname_history or []
        history.append({"nickname": old, "time": datetime.now().isoformat()})
        account.nickname_history = history
    account.nickname = new_nickname


class AddAccountRequest(BaseModel):
    platform: str  # qq / xhs
    account_id: str
    nickname: Optional[str] = None
    is_target: int = 1


@router.post("/accounts")
async def add_account(req: AddAccountRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(
        select(Account).where(Account.platform == req.platform, Account.account_id == req.account_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="账号已存在")
    account = Account(
        platform=req.platform,
        account_id=req.account_id,
        nickname=req.nickname,
        is_target=req.is_target,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return {"id": account.id, "message": "添加成功"}


@router.get("/accounts")
async def get_accounts(platform: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    query = select(Account)
    if platform:
        query = query.where(Account.platform == platform)
    result = await db.execute(query.order_by(Account.created_at.desc()))
    accounts = result.scalars().all()
    return [
        {
            "id": a.id,
            "platform": a.platform,
            "account_id": a.account_id,
            "platform_uid": a.platform_uid,
            "nickname": a.nickname,
            "avatar_url": _verify_avatar(a.avatar_url, a.platform, a.account_id),
            "avatar_history": a.avatar_history or [],
            "nickname_history": a.nickname_history or [],
            "signature": a.signature,
            "status": a.status,
            "is_target": a.is_target,
            "last_login": a.last_login.isoformat() if a.last_login else None,
            "cookie_last_validated_at": a.cookie_last_validated_at.isoformat() if a.cookie_last_validated_at else None,
            "last_cookie_refresh_at": a.last_cookie_refresh_at.isoformat() if a.last_cookie_refresh_at else None,
            "last_failure_at": a.last_failure_at.isoformat() if a.last_failure_at else None,
            "last_failure_reason": a.last_failure_reason,
            "risk_cooldown_until": a.risk_cooldown_until.isoformat() if a.risk_cooldown_until else None,
            "failure_count": a.failure_count or 0,
            "is_in_cooldown": is_account_in_cooldown(a),
        }
        for a in accounts
    ]


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    await db.delete(account)
    await db.commit()
    return {"message": "删除成功"}


@router.post("/accounts/{account_id}/refresh")
async def refresh_account(account_id: int, db: AsyncSession = Depends(get_db)):
    """刷新账号信息（昵称、头像、签名等）— 支持监控账号和登录账号"""
    import logging
    _logger = logging.getLogger("legacy.routers.auth")
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    try:
        if account.platform == "xhs":
            from legacy.database import async_session as _async_session
            from legacy.services.account_risk_service import get_preferred_login_account
            import json

            # 获取登录Cookie
            login_account = account if account.is_target == 0 else None
            if not login_account:
                async with _async_session() as db2:
                    login_account = await get_preferred_login_account(
                        db2,
                        "xhs",
                        require_cookies=True,
                        allow_degraded=True,
                    )

            if not login_account or not login_account.cookies:
                raise HTTPException(status_code=503, detail="小红书未登录，请先扫码登录")

            cookies = json.loads(login_account.cookies)

            # 确定要查询的用户ID（必须是ObjectId格式）
            import re
            target_uid = account.platform_uid or account.account_id
            # 如果不是ObjectId格式，尝试先解析
            if not re.match(r'^[a-f0-9]{24}$', target_uid):
                # 红薯号, 需要先解析ObjectId
                from legacy.services.xhs_crawler import xhs_crawler
                from playwright.async_api import async_playwright
                import os
                p = await async_playwright().start()
                browser_data_dir = os.path.join(
                    os.path.dirname(os.path.abspath(_settings.static_dir)),
                    "browser_data", "xhs_crawl"
                )
                os.makedirs(browser_data_dir, exist_ok=True)
                context = await p.chromium.launch_persistent_context(
                    browser_data_dir,
                    headless=True,
                    args=['--no-sandbox'],
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                )
                await context.clear_cookies()
                await context.add_cookies(cookies)
                page = context.pages[0] if context.pages else await context.new_page()
                try:
                    await page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(2)
                    resolved = await xhs_crawler._resolve_uid(target_uid, page)
                    if resolved:
                        target_uid = resolved
                        account.platform_uid = resolved
                finally:
                    await context.close()
                    await p.stop()

            if not re.match(r'^[a-f0-9]{24}$', target_uid):
                raise HTTPException(status_code=400, detail=f"无法解析用户ID: {target_uid}")

            try:
                from playwright.async_api import async_playwright
                import asyncio, os

                p = await async_playwright().start()
                browser_data_dir = os.path.join(
                    os.path.dirname(os.path.abspath(_settings.static_dir)),
                    "browser_data", "xhs_crawl"
                )
                os.makedirs(browser_data_dir, exist_ok=True)
                context = await p.chromium.launch_persistent_context(
                    browser_data_dir,
                    headless=True,
                    args=['--no-sandbox'],
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                )
                await context.clear_cookies()
                await context.add_cookies(cookies)
                page = context.pages[0] if context.pages else await context.new_page()

                captured_info = {}

                async def on_response(response):
                    url = response.url
                    if response.status == 200 and ('user/otherinfo' in url or 'user/selfinfo' in url):
                        try:
                            body = await response.json()
                            data = body.get("data", {})
                            if isinstance(data, dict) and data:
                                captured_info.update(data)
                                _logger.info(f"XHS刷新拦截到用户信息API: {list(data.keys())[:10]}")
                        except Exception:
                            pass

                page.on('response', on_response)

                try:
                    # 导航到用户主页
                    await page.goto(
                        f"https://www.xiaohongshu.com/user/profile/{target_uid}",
                        wait_until="networkidle", timeout=20000
                    )
                    await asyncio.sleep(4)

                    # 检测Cookie是否过期（页面被重定向到登录页）
                    current_url = page.url
                    current_title = await page.title()
                    if 'login' in current_url.lower() or 'captcha' in current_url or current_title == '':
                        _logger.warning(f"XHS Cookie已过期，刷新时被重定向到登录页: {current_url}")
                        from legacy.services.xhs_crawler import xhs_crawler
                        xhs_crawler.login_status = "expired"
                        xhs_crawler.login_status_detail = "Cookie已过期"
                        raise HTTPException(status_code=503, detail="小红书Cookie已过期，请重新扫码登录")

                    # 优先从DOM元素解析用户信息（最可靠）
                    user_info = await page.evaluate("""() => {
                        const info = {};
                        // 昵称
                        const nameEl = document.querySelector('.user-name, [class*="user-name"]');
                        if (nameEl) info.nickname = (nameEl.textContent || '').trim();
                        // 红薯号
                        const redIdEl = document.querySelector('.user-redId, [class*="user-redId"]');
                        if (redIdEl) {
                            const text = redIdEl.textContent || '';
                            const m = text.match(/[：:]\\s*(\\S+)/);
                            if (m) info.red_id = m[1];
                        }
                        // 头像（去掉模糊参数）
                        const avatarEl = document.querySelector('.user-image, [class*="user-image"]');
                        if (avatarEl) {
                            let src = avatarEl.getAttribute('src') || '';
                            src = src.split('|')[0];
                            info.avatar = src;
                        }
                        // 个人简介
                        const descEl = document.querySelector('.user-desc, [class*="user-desc"]');
                        if (descEl) info.desc = (descEl.textContent || '').trim();
                        // __INITIAL_STATE__ 兜底
                        if (!info.nickname || !info.red_id) {
                            try {
                                const state = window.__INITIAL_STATE__;
                                if (state) {
                                    let s = JSON.stringify(state).replace(/undefined/g, 'null');
                                    const parsed = JSON.parse(s);
                                    const userPage = parsed.user?.userPageData;
                                    if (userPage) {
                                        if (!info.nickname) info.nickname = userPage.basicInfo?.nickname || '';
                                        if (!info.red_id) info.red_id = userPage.basicInfo?.redId || '';
                                        if (!info.avatar) info.avatar = userPage.basicInfo?.imageb || userPage.basicInfo?.image || '';
                                        if (!info.desc) info.desc = userPage.basicInfo?.desc || '';
                                        info.user_id = userPage.basicInfo?.userId || '';
                                    }
                                    if (!info.nickname) {
                                        const m = s.match(/"nickname"\\s*:\\s*"([^"]{1,50})"/);
                                        if (m) info.nickname = m[1];
                                    }
                                    if (!info.red_id) {
                                        const m = s.match(/"redId"\\s*:\\s*"([^"]+)"/);
                                        if (m) info.red_id = m[1];
                                    }
                                }
                            } catch(e) {}
                        }
                        return info;
                    }""")

                    # 合并信息（API拦截优先）
                    info = {**user_info}
                    if captured_info:
                        # API返回的信息可能用不同字段名
                        api_info = captured_info
                        if api_info.get("nickname"):
                            info["nickname"] = api_info["nickname"]
                        if api_info.get("imageb") or api_info.get("image"):
                            info["avatar"] = api_info.get("imageb") or api_info.get("image")
                        if api_info.get("desc"):
                            info["desc"] = api_info["desc"]
                        if api_info.get("red_id") or api_info.get("redId"):
                            info["red_id"] = api_info.get("red_id") or api_info.get("redId")

                    # 下载头像到本地
                    from legacy.services.media_service import media_service
                    avatar_remote = info.get("avatar", "")
                    local_avatar = None
                    if avatar_remote:
                        local_avatar = await media_service.download_image(avatar_remote, "avatar")

                    # 更新账号信息
                    if info.get("nickname"):
                        _update_nickname(account, info["nickname"])
                    
                    new_avatar = local_avatar or avatar_remote
                    if new_avatar and new_avatar != account.avatar_url:
                        # 头像变更，记录历史
                        if account.avatar_url:
                            history = account.avatar_history or []
                            history.append({
                                "url": account.avatar_url,
                                "time": datetime.now().isoformat(),
                            })
                            account.avatar_history = history
                        account.avatar_url = new_avatar
                    
                    if info.get("desc"):
                        account.signature = info["desc"]
                    if info.get("user_id"):
                        account.platform_uid = info["user_id"]
                    if info.get("red_id") and account.is_target == 0:
                        _logger.info(f"XHS登录账号红薯号: {info['red_id']}")

                    _logger.info(f"XHS账号 {account.account_id} 信息刷新成功: nickname={account.nickname}, avatar={'有' if new_avatar else '无'}")

                finally:
                    page.remove_listener('response', on_response)
                    await context.close()
                    await p.stop()

            except ImportError:
                raise HTTPException(status_code=503, detail="Playwright未安装")

        elif account.platform == "qq":
            import aiohttp, ssl as _ssl, json, re
            from legacy.services.media_service import media_service
            from legacy.services.account_risk_service import get_preferred_login_account
            from legacy.services.qq_crawler import compute_g_tk

            # 加载QQ登录Cookie（portrait API需要鉴权）
            from legacy.database import async_session as _async_session
            cookies_dict = {}
            g_tk = 0
            async with _async_session() as db2:
                login_acc = await get_preferred_login_account(
                    db2,
                    "qq",
                    require_cookies=True,
                    allow_degraded=True,
                )
                if login_acc and login_acc.cookies:
                    cookies_list = json.loads(login_acc.cookies)
                    for c in cookies_list:
                        cookies_dict[c.get("name", "")] = c.get("value", "")
                    p_skey = cookies_dict.get("p_skey", "") or cookies_dict.get("skey", "")
                    if p_skey:
                        g_tk = compute_g_tk(p_skey)
                        _logger.info(f"QQ refresh: 已加载登录Cookie, g_tk={g_tk}")

            if not g_tk:
                _logger.warning("QQ refresh: 无可用的登录Cookie，portrait API可能返回空数据")

            ssl_ctx = _ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = _ssl.CERT_NONE
            headers_qq = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

            # 准备两个portrait URL: 公开API不需要cookies, r.qzone需要cookies
            portrait_confs = [
                {"url": f"https://users.qzone.qq.com/fcg-bin/cgi_get_portrait.fcg?uins={account.account_id}", "cookies": {}},
                {"url": f"https://r.qzone.qq.com/fcg-bin/cgi_get_portrait.fcg?g_tk={g_tk}&uins={account.account_id}", "cookies": cookies_dict, "headers": {"Referer": "https://user.qzone.qq.com/"}},
            ]
            try:
                portrait_success = False
                for p_conf in portrait_confs:
                    try:
                        p_url = p_conf["url"]
                        p_cookies = p_conf.get("cookies", {})
                        p_headers = {**headers_qq, **p_conf.get("headers", {})}
                        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
                        async with aiohttp.ClientSession(connector=connector, cookies=p_cookies, headers=p_headers) as session:
                            async with session.get(p_url) as resp:
                                raw_bytes = await resp.read()
                                ct = resp.headers.get('Content-Type', '')
                                if 'utf-8' in ct.lower():
                                    text = raw_bytes.decode('utf-8', errors='ignore')
                                else:
                                    text = raw_bytes.decode('gbk', errors='ignore')
                                _logger.info(f"QQ portrait API ({p_url[:50]}...): {text[:200]}")
                                m = re.search(r'portraitCallBack\((.+)\)', text)
                                if m:
                                    data = json.loads(m.group(1))
                                    info = data.get(account.account_id, [])
                                    if len(info) > 0 and info[0]:
                                        avatar_url = info[0]
                                        if not avatar_url.startswith("http"):
                                            avatar_url = f"https://q.qlogo.cn/headimg_dl?dst_uin={account.account_id}&spec=640&img_type=jpg"
                                        local_avatar = await media_service.download_image(avatar_url, "avatar")
                                        new_avatar = local_avatar or avatar_url
                                        if new_avatar != account.avatar_url and account.avatar_url:
                                            history = account.avatar_history or []
                                            history.append({"url": account.avatar_url, "time": datetime.now().isoformat()})
                                            account.avatar_history = history
                                        account.avatar_url = new_avatar
                                    if len(info) > 6 and info[6]:
                                        nickname = info[6].replace('\ufffd', '').strip()
                                        if not nickname or not any('\u4e00' <= c <= '\u9fff' for c in nickname):
                                            try:
                                                gbk_text = raw_bytes.decode('gbk', errors='ignore')
                                                m2 = re.search(r'portraitCallBack\((.+)\)', gbk_text)
                                                if m2:
                                                    gbk_data = json.loads(m2.group(1))
                                                    gbk_info = gbk_data.get(account.account_id, [])
                                                    if len(gbk_info) > 6 and gbk_info[6]:
                                                        gbk_nick = gbk_info[6].replace('锟斤拷', '').replace('锟', '').strip()
                                                        if gbk_nick and any('\u4e00' <= c <= '\u9fff' for c in gbk_nick):
                                                            nickname = gbk_nick
                                            except Exception:
                                                pass
                                        if nickname:
                                            _update_nickname(account, nickname)
                                    portrait_success = True
                                    _logger.info(f"QQ账号 {account.account_id} 刷新成功: nick={account.nickname}, avatar={'有' if account.avatar_url else '无'}")
                                    break
                    except Exception as e_inner:
                        _logger.debug(f"QQ portrait API失败 ({p_conf['url'][:50]}): {e_inner}")
                        continue

                if not portrait_success:
                    _logger.warning(f"QQ portrait API全部失败，用q.qlogo.cn兜底")
                    avatar_cdn = f"https://q.qlogo.cn/headimg_dl?dst_uin={account.account_id}&spec=640&img_type=jpg"
                    local_avatar = await media_service.download_image(avatar_cdn, "avatar")
                    if local_avatar:
                        if account.avatar_url and account.avatar_url != local_avatar:
                            history = account.avatar_history or []
                            history.append({"url": account.avatar_url, "time": datetime.now().isoformat()})
                            account.avatar_history = history
                        account.avatar_url = local_avatar
            except Exception as e:
                _logger.warning(f"QQ头像API失败: {e}，尝试q.qlogo.cn兜底")
                avatar_cdn = f"https://q.qlogo.cn/headimg_dl?dst_uin={account.account_id}&spec=640&img_type=jpg"
                local_avatar = await media_service.download_image(avatar_cdn, "avatar")
                if local_avatar and not account.avatar_url:
                    account.avatar_url = local_avatar

            # 从帖子数据获取更可靠的昵称（优先于portrait API的乱码结果）
            from legacy.models.qq_post import QQPost
            from sqlalchemy import select as _sel2, desc
            post_r = await db.execute(
                _sel2(QQPost.author_nickname).where(
                    QQPost.author_qq == account.account_id,
                    QQPost.author_nickname != "",
                    QQPost.author_nickname.isnot(None),
                ).order_by(desc(QQPost.post_time)).limit(1)
            )
            post_nick = post_r.scalar_one_or_none()
            if post_nick and len(post_nick) > len(account.nickname or ''):
                _update_nickname(account, post_nick)

            # 获取QQ签名（个性签名），尝试多个QQ空间API
            if g_tk and cookies_dict:
                try:
                    sig_apis = [
                        f"https://r.qzone.qq.com/cgi-bin/user/cgi_personal_card?uin={account.account_id}&g_tk={g_tk}&fupdate=1",
                        f"https://users.qzone.qq.com/cgi-bin/feeds/feeds_html_act_v2?uin={account.account_id}&g_tk={g_tk}&format=json",
                        f"https://user.qzone.qq.com/proxy/domain/base.qzone.qq.com/cgi-bin/user/cgi_userinfo_get_all?uin={account.account_id}&g_tk={g_tk}&vuin={cookies_dict.get('uin', '').lstrip('o0')}",
                    ]
                    sign_headers = {**headers_qq, "Referer": f"https://user.qzone.qq.com/{account.account_id}"}
                    sig_found = False
                    for sig_url in sig_apis:
                        if sig_found:
                            break
                        try:
                            connector2 = aiohttp.TCPConnector(ssl=ssl_ctx)
                            async with aiohttp.ClientSession(connector=connector2, cookies=cookies_dict, headers=sign_headers) as sign_session:
                                async with sign_session.get(sig_url) as sign_resp:
                                    sign_raw = await sign_resp.read()
                                    sign_text = sign_raw.decode('utf-8', errors='ignore')
                                    if not sign_text:
                                        sign_text = sign_raw.decode('gbk', errors='ignore')
                                    _logger.debug(f"QQ签名API({sig_url[:50]}): {sign_text[:200]}")
                                    # 尝试多种字段匹配签名
                                    for field in ('signature', 'mood', 'lnick', 'desc'):
                                        sign_m = re.search(rf'"{field}"\s*:\s*"((?:[^"\\]|\\.)*)"', sign_text)
                                        if sign_m:
                                            sig_val = sign_m.group(1).strip()
                                            if sig_val and sig_val not in ('null', 'undefined', '""'):
                                                try:
                                                    sig_val = sig_val.encode('raw_unicode_escape').decode('unicode_escape')
                                                except Exception:
                                                    pass
                                                account.signature = sig_val
                                                _logger.info(f"QQ签名获取成功 ({field}): {sig_val[:50]}")
                                                sig_found = True
                                                break
                        except Exception:
                            continue
                except Exception as e_sig:
                    _logger.debug(f"QQ签名获取失败（非致命）: {e_sig}")

        await db.commit()
        await db.refresh(account)
        return {
            "id": account.id,
            "platform": account.platform,
            "account_id": account.account_id,
            "platform_uid": account.platform_uid,
            "nickname": account.nickname,
            "avatar_url": _verify_avatar(account.avatar_url, account.platform, account.account_id),
            "avatar_history": account.avatar_history or [],
            "nickname_history": account.nickname_history or [],
            "signature": account.signature,
            "status": account.status,
            "message": "刷新成功",
        }
    except HTTPException:
        raise
    except Exception as e:
        _logger.error(f"刷新账号信息失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"刷新失败: {e}")


@router.post("/accounts/refresh-all")
async def refresh_all_accounts(db: AsyncSession = Depends(get_db)):
    """一键刷新所有账号信息"""
    import logging
    _logger = logging.getLogger("legacy.routers.auth")
    result = await db.execute(select(Account).order_by(Account.id))
    accounts = result.scalars().all()
    results = []
    for account in accounts:
        try:
            # 调用单个刷新逻辑（通过内部函数）
            # 由于每个refresh_account需要独立db session，直接调用endpoint
            from legacy.database import async_session as _async_session
            async with _async_session() as db2:
                r = await db2.execute(select(Account).where(Account.id == account.id))
                acc = r.scalars().first()
                if not acc:
                    continue
                
                if acc.platform == "qq":
                    import aiohttp, ssl as _ssl, json, re
                    from legacy.services.media_service import media_service
                    from legacy.services.qq_crawler import compute_g_tk

                    # 加载QQ登录Cookie
                    cookies_dict = {}
                    g_tk = 0
                    login_r = await db2.execute(
                        select(Account).where(
                            Account.platform == "qq", Account.status == "active", Account.is_target == 0
                        ).order_by(Account.last_login.desc())
                    )
                    login_acc = login_r.scalars().first()
                    if login_acc and login_acc.cookies:
                        cookies_list = json.loads(login_acc.cookies)
                        for c in cookies_list:
                            cookies_dict[c.get("name", "")] = c.get("value", "")
                        p_skey = cookies_dict.get("p_skey", "") or cookies_dict.get("skey", "")
                        if p_skey:
                            g_tk = compute_g_tk(p_skey)

                    ssl_ctx = _ssl.create_default_context()
                    ssl_ctx.check_hostname = False
                    ssl_ctx.verify_mode = _ssl.CERT_NONE
                    headers_qq = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                    portrait_confs = [
                        {"url": f"https://users.qzone.qq.com/fcg-bin/cgi_get_portrait.fcg?uins={acc.account_id}", "cookies": {}},
                        {"url": f"https://r.qzone.qq.com/fcg-bin/cgi_get_portrait.fcg?g_tk={g_tk}&uins={acc.account_id}", "cookies": cookies_dict, "headers": {"Referer": "https://user.qzone.qq.com/"}},
                    ]
                    portrait_ok = False
                    for p_conf in portrait_confs:
                        try:
                            p_url = p_conf["url"]
                            p_cookies = p_conf.get("cookies", {})
                            p_headers_extra = {**headers_qq, **p_conf.get("headers", {})}
                            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
                            async with aiohttp.ClientSession(connector=connector, cookies=p_cookies, headers=p_headers_extra) as session:
                                async with session.get(p_url) as resp:
                                    raw_bytes = await resp.read()
                                    ct = resp.headers.get('Content-Type', '')
                                    if 'utf-8' in ct.lower():
                                        text = raw_bytes.decode('utf-8', errors='ignore')
                                    else:
                                        text = raw_bytes.decode('gbk', errors='ignore')
                                    m = re.search(r'portraitCallBack\((.+)\)', text)
                                    if m:
                                        data = json.loads(m.group(1))
                                        info = data.get(acc.account_id, [])
                                        if len(info) > 0 and info[0]:
                                            avatar_url = info[0] if info[0].startswith("http") else f"https://q.qlogo.cn/headimg_dl?dst_uin={acc.account_id}&spec=640&img_type=jpg"
                                            local_avatar = await media_service.download_image(avatar_url, "avatar")
                                            new_av = local_avatar or avatar_url
                                            if new_av != acc.avatar_url and acc.avatar_url:
                                                history = acc.avatar_history or []
                                                history.append({"url": acc.avatar_url, "time": datetime.now().isoformat()})
                                                acc.avatar_history = history
                                            acc.avatar_url = new_av
                                        if len(info) > 6 and info[6]:
                                            nickname = info[6].replace('\ufffd', '').strip()
                                            if not nickname or not any('\u4e00' <= c <= '\u9fff' for c in nickname):
                                                try:
                                                    gbk_text = raw_bytes.decode('gbk', errors='ignore')
                                                    m2 = re.search(r'portraitCallBack\((.+)\)', gbk_text)
                                                    if m2:
                                                        gbk_data = json.loads(m2.group(1))
                                                        gbk_info = gbk_data.get(acc.account_id, [])
                                                        if len(gbk_info) > 6 and gbk_info[6]:
                                                            gbk_nick = gbk_info[6].replace('锟斤拷', '').replace('锟', '').strip()
                                                            if gbk_nick and any('\u4e00' <= c <= '\u9fff' for c in gbk_nick):
                                                                nickname = gbk_nick
                                                except Exception:
                                                    pass
                                            if nickname:
                                                _update_nickname(acc, nickname)
                                        portrait_ok = True
                                        break
                        except Exception:
                            continue
                    if not portrait_ok:
                        avatar_cdn = f"https://q.qlogo.cn/headimg_dl?dst_uin={acc.account_id}&spec=640&img_type=jpg"
                        local_av = await media_service.download_image(avatar_cdn, "avatar")
                        if local_av:
                            if acc.avatar_url and acc.avatar_url != local_av:
                                history = acc.avatar_history or []
                                history.append({"url": acc.avatar_url, "time": datetime.now().isoformat()})
                                acc.avatar_history = history
                            acc.avatar_url = local_av

                    # 从帖子数据获取更可靠的昵称
                    from legacy.models.qq_post import QQPost
                    from sqlalchemy import desc
                    post_r = await db2.execute(
                        select(QQPost.author_nickname).where(
                            QQPost.author_qq == acc.account_id,
                            QQPost.author_nickname != "",
                            QQPost.author_nickname.isnot(None),
                        ).order_by(desc(QQPost.post_time)).limit(1)
                    )
                    post_nick = post_r.scalar_one_or_none()
                    if post_nick and len(post_nick) > len(acc.nickname or ''):
                        _update_nickname(acc, post_nick)

                    await db2.commit()
                    results.append({"id": acc.id, "status": "ok"})
                else:
                    results.append({"id": acc.id, "status": "skipped", "reason": "XHS requires browser"})
        except Exception as e:
            _logger.warning(f"刷新账号 {account.id} 失败: {e}")
            results.append({"id": account.id, "status": "error", "reason": str(e)})
    
    return {"results": results, "message": f"已刷新 {len([r for r in results if r['status'] == 'ok'])} 个账号"}


@router.post("/accounts/{account_id}/toggle")
async def toggle_account(account_id: int, db: AsyncSession = Depends(get_db)):
    """启用/禁用账号"""
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    if account.status == DISABLED_STATUS:
        if account.is_target == 0 and ((account.failure_count or 0) > 0 or not account.cookies):
            account.status = RELOGIN_PENDING_STATUS
        else:
            account.status = ACTIVE_STATUS
            account.risk_cooldown_until = None
    else:
        account.status = DISABLED_STATUS
    await db.commit()
    return {"id": account.id, "status": account.status, "message": f"已{'禁用' if account.status == 'disabled' else '启用'}"}


@router.get("/qq/qrcode")
async def get_qq_qrcode():
    """通过Playwright获取QQ空间Web登录二维码"""
    from legacy.services.qq_crawler import qq_crawler
    import logging
    _logger = logging.getLogger("legacy.routers.auth")
    try:
        _logger.info("收到QQ二维码请求，开始获取...")
        qrcode = await qq_crawler.get_login_qrcode()
        if not qrcode:
            _logger.error(f"QQ二维码获取返回空，login_status={qq_crawler.login_status}")
            raise HTTPException(status_code=503, detail="无法获取QQ空间登录二维码，请查看后端日志")
        _logger.info(f"QQ二维码获取成功，数据长度={len(qrcode)}")
        return {"qrcode": qrcode}
    except HTTPException:
        raise
    except Exception as e:
        _logger.error(f"QQ二维码接口异常: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/qq/status")
async def get_qq_login_status():
    from legacy.services.qq_crawler import qq_crawler
    return {
        "status": qq_crawler.login_status,
        "detail": getattr(qq_crawler, "login_status_detail", ""),
    }  # unknown/getting_qrcode/waiting_scan/logged_in/expired/error


@router.post("/qq/napcat-login")
async def qq_napcat_login():
    """通过NapCat OneBot11 API获取QQ空间Cookie实现免扫码登录"""
    from legacy.services.qq_crawler import qq_crawler
    import logging
    _logger = logging.getLogger("legacy.routers.auth")
    try:
        _logger.info("收到NapCat自动登录请求")
        result = await qq_crawler.login_via_napcat()
        if result.get("success"):
            _logger.info(f"NapCat登录成功: {result.get('message')}")
            return result
        else:
            _logger.warning(f"NapCat登录失败: {result.get('message')}")
            raise HTTPException(status_code=400, detail=result.get("message", "NapCat登录失败"))
    except HTTPException:
        raise
    except Exception as e:
        _logger.error(f"NapCat登录接口异常: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/qq/debug-view")
async def get_qq_debug_view():
    from legacy.services.qq_crawler import qq_crawler
    return await qq_crawler.get_login_debug_view()


@router.post("/qq/refresh-cookies")
async def refresh_qq_cookies():
    """手动触发QQ空间Cookie续期"""
    from legacy.services.qq_crawler import qq_crawler
    try:
        success = await qq_crawler.refresh_qq_cookies(allow_degraded=True)
        if success:
            return {"success": True, "message": "Cookie续期成功"}
        else:
            return {"success": False, "message": "Cookie续期失败，可能需要重新扫码登录"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cookie续期异常: {e}")


@router.get("/xhs/qrcode")
async def get_xhs_qrcode():
    """通过Playwright获取小红书登录二维码截图"""
    from legacy.services.xhs_crawler import xhs_crawler
    import logging
    _logger = logging.getLogger("legacy.routers.auth")
    try:
        _logger.info("收到小红书二维码请求，开始获取...")
        qrcode = await xhs_crawler.get_login_qrcode()
        if not qrcode:
            _logger.error(f"小红书二维码获取返回空，login_status={xhs_crawler.login_status}")
            raise HTTPException(status_code=503, detail="无法获取小红书登录二维码，请查看后端日志")
        _logger.info(f"小红书二维码获取成功，数据长度={len(qrcode)}")
        return {"qrcode": qrcode}
    except HTTPException:
        raise
    except Exception as e:
        _logger.error(f"小红书二维码接口异常: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/xhs/status")
async def get_xhs_login_status():
    from legacy.services.xhs_crawler import xhs_crawler
    status = xhs_crawler.login_status
    detail = getattr(xhs_crawler, "login_status_detail", "")

    # 防止误报：如果状态是 logged_in 但没有活跃浏览器会话，
    # 验证数据库中是否有有效的XHS登录账号的cookie
    if status == "logged_in" and not xhs_crawler._browser_context:
        try:
            from legacy.database import async_session
            from legacy.models.account import Account
            from sqlalchemy import select
            async with async_session() as db:
                result = await db.execute(
                    select(Account).where(
                        Account.platform == "xhs",
                        Account.is_target == 0,
                        Account.status == "active",
                    )
                )
                login_accounts = result.scalars().all()
                if not login_accounts or not any(a.cookies for a in login_accounts):
                    # 数据库中无有效cookie，状态不可信
                    status = "unknown"
                    detail = "上次登录的cookie可能已过期，请重新扫码"
                    xhs_crawler.login_status = "unknown"
                    xhs_crawler.login_status_detail = detail
        except Exception:
            pass

    return {
        "status": status,
        "detail": detail,
    }


@router.get("/xhs/debug-view")
async def get_xhs_debug_view():
    from legacy.services.xhs_crawler import xhs_crawler
    return await xhs_crawler.get_login_debug_view()


@router.post("/xhs/navigate")
async def xhs_navigate(request: Request):
    """在XHS浏览器中导航到指定URL"""
    from legacy.services.xhs_crawler import xhs_crawler
    body = await request.json()
    url = body.get("url", "")
    if not url:
        raise HTTPException(status_code=400, detail="缺少url参数")
    return await xhs_crawler.navigate_to(url)


@router.post("/xhs/save-cookies")
async def xhs_save_cookies():
    """手动保存当前XHS浏览器会话的Cookie"""
    from legacy.services.xhs_crawler import xhs_crawler
    return await xhs_crawler.save_cookies_manual()


@router.post("/xhs/reset-browser")
async def xhs_reset_browser():
    """重置XHS浏览器指纹和cookie数据"""
    from legacy.services.xhs_crawler import xhs_crawler
    return await xhs_crawler.reset_browser_data()

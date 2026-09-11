"""登录流程的 v2 入口。

二维码浏览器会话暂由已验证的登录实现维持；登录完成后立即把凭据同步到新身份域，
采集引擎只读取 ``login_credentials``。
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text

from app.api.deps import AdminDep, SessionDep
from app.core.config import settings
from app.core.events import EventType, event_bus
from app.domain.enums import CredentialStatus
from app.domain.identity import BrowserProfile, LoginCredential

router = APIRouter()


class NavigateRequest(BaseModel):
    url: str


async def _sync_latest_credential(platform: str, session: SessionDep) -> LoginCredential | None:
    """从登录会话落地表同步到新凭据表；不修改或删除旧记录。"""
    result = await session.execute(
        text(
            """
            SELECT account_id, platform_uid, nickname, cookies
            FROM accounts
            WHERE platform = :platform AND is_target = 0 AND cookies IS NOT NULL
            ORDER BY last_login DESC NULLS LAST, id DESC
            LIMIT 1
            """
        ),
        {"platform": platform},
    )
    source = result.mappings().first()
    if source is None:
        return None
    cookies = source["cookies"]
    if isinstance(cookies, str):
        try:
            cookies = json.loads(cookies)
        except json.JSONDecodeError:
            return None
    if not isinstance(cookies, list) or not cookies:
        return None

    label = str(source["account_id"] or source["platform_uid"] or platform)
    credential = (
        await session.execute(
            select(LoginCredential)
            .where(LoginCredential.platform == platform)
            .order_by(LoginCredential.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    profile_name = f"{platform}-login-{label}"
    profile = (
        await session.execute(select(BrowserProfile).where(BrowserProfile.name == profile_name))
    ).scalar_one_or_none()
    if profile is None:
        profile_dir = settings.browser_profile_path / ("xhs_login" if platform == "xhs" else "qq_login")
        profile = BrowserProfile(
            name=profile_name,
            platform=platform,
            fingerprint={},
            profile_dir=str(profile_dir),
            notes="v2 登录凭据固定浏览器档案",
        )
        session.add(profile)
        await session.flush()
    now = datetime.now()
    if credential is None:
        credential = LoginCredential(platform=platform, account_label=label)
        session.add(credential)
    credential.account_label = label
    credential.platform_uid = str(source["platform_uid"] or source["account_id"] or "") or None
    credential.nickname = source["nickname"]
    credential.cookies = cookies
    credential.status = CredentialStatus.ACTIVE.value
    credential.is_enabled = True
    credential.failure_count = 0
    credential.last_failure_at = None
    credential.last_failure_reason = None
    credential.cooldown_until = None
    credential.last_validated_at = now
    credential.last_refreshed_at = now
    credential.last_login_at = now
    credential.browser_profile_id = profile.id
    await session.commit()
    await event_bus.publish(
        EventType.LOGIN_SUCCEEDED,
        {"platform": platform, "credential_id": credential.id},
    )
    return credential


@router.get("/qq/qrcode")
async def qq_qrcode(_: AdminDep):
    from legacy.services.qq_crawler import qq_crawler

    value = await qq_crawler.get_login_qrcode()
    if not value:
        raise HTTPException(status_code=503, detail="无法获取 QQ 登录二维码")
    return {"qrcode": value}


@router.get("/qq/status")
async def qq_status(session: SessionDep, _: AdminDep):
    from legacy.services.qq_crawler import qq_crawler

    if qq_crawler.login_status == "logged_in":
        await _sync_latest_credential("qq", session)
    return {"status": qq_crawler.login_status, "detail": qq_crawler.login_status_detail}


@router.get("/qq/debug-view")
async def qq_debug_view(_: AdminDep):
    from legacy.services.qq_crawler import qq_crawler

    return await qq_crawler.get_login_debug_view()


@router.post("/qq/refresh-cookies")
async def qq_refresh_cookies(session: SessionDep, _: AdminDep):
    from legacy.services.qq_crawler import qq_crawler

    success = await qq_crawler.refresh_qq_cookies(allow_degraded=True)
    if success:
        await _sync_latest_credential("qq", session)
    return {"success": success, "message": "Cookie 续期成功" if success else "Cookie 续期失败，请重新扫码"}


@router.post("/qq/napcat-login")
async def qq_napcat_login(session: SessionDep, _: AdminDep):
    from legacy.services.qq_crawler import qq_crawler

    result = await qq_crawler.login_via_napcat()
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "NapCat 登录失败"))
    await _sync_latest_credential("qq", session)
    return result


@router.get("/xhs/qrcode")
async def xhs_qrcode(_: AdminDep):
    from legacy.services.xhs_crawler import xhs_crawler

    value = await xhs_crawler.get_login_qrcode()
    if not value:
        raise HTTPException(status_code=503, detail="无法获取小红书登录二维码")
    return {"qrcode": value}


@router.get("/xhs/status")
async def xhs_status(session: SessionDep, _: AdminDep):
    from legacy.services.xhs_crawler import xhs_crawler

    if xhs_crawler.login_status == "logged_in":
        await _sync_latest_credential("xhs", session)
    return {"status": xhs_crawler.login_status, "detail": xhs_crawler.login_status_detail}


@router.get("/xhs/debug-view")
async def xhs_debug_view(_: AdminDep):
    from legacy.services.xhs_crawler import xhs_crawler

    return await xhs_crawler.get_login_debug_view()


@router.post("/xhs/navigate")
async def xhs_navigate(payload: NavigateRequest, _: AdminDep):
    from legacy.services.xhs_crawler import xhs_crawler

    return await xhs_crawler.navigate_to(payload.url)


@router.post("/xhs/save-cookies")
async def xhs_save_cookies(session: SessionDep, _: AdminDep):
    from legacy.services.xhs_crawler import xhs_crawler

    result = await xhs_crawler.save_cookies_manual()
    if result.get("ok"):
        await _sync_latest_credential("xhs", session)
    return result


@router.post("/xhs/reset-browser")
async def xhs_reset_browser(_: AdminDep):
    from legacy.services.xhs_crawler import xhs_crawler

    return await xhs_crawler.reset_browser_data()

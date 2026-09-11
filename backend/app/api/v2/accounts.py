"""登录凭据与监控目标。"""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import AdminDep, SessionDep
from app.core.errors import NotFoundError
from app.domain.enums import Platform
from app.domain.identity import LoginCredential, PlatformAccount

router = APIRouter()


class TargetCreate(BaseModel):
    platform: Platform
    account_id: str
    platform_uid: str | None = None
    nickname: str | None = None


@router.get("/targets", summary="监控目标")
async def list_targets(session: SessionDep, _: AdminDep, platform: Platform | None = Query(None)):
    stmt = select(PlatformAccount)
    if platform:
        stmt = stmt.where(PlatformAccount.platform == platform.value)
    rows = (await session.execute(stmt.order_by(PlatformAccount.id))).scalars().all()
    return {"items": [_target(row) for row in rows]}


@router.post("/targets", summary="添加监控目标")
async def create_target(payload: TargetCreate, session: SessionDep, _: AdminDep):
    row = PlatformAccount(
        platform=payload.platform.value,
        account_id=payload.account_id,
        platform_uid=payload.platform_uid or payload.account_id,
        nickname=payload.nickname,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _target(row)


@router.delete("/targets/{target_id}", summary="删除监控目标")
async def delete_target(target_id: int, session: SessionDep, _: AdminDep):
    row = await session.get(PlatformAccount, target_id)
    if row is None:
        raise NotFoundError("监控目标不存在")
    await session.delete(row)
    await session.commit()
    return {"ok": True}


@router.post("/targets/{target_id}/toggle", summary="启用/停用监控目标")
async def toggle_target(target_id: int, session: SessionDep, _: AdminDep):
    row = await session.get(PlatformAccount, target_id)
    if row is None:
        raise NotFoundError("监控目标不存在")
    row.is_enabled = not row.is_enabled
    await session.commit()
    return {**_target(row), "message": "已启用" if row.is_enabled else "已停用"}


@router.post("/targets/{target_id}/refresh", summary="刷新目标资料")
async def refresh_target(target_id: int, session: SessionDep, _: AdminDep):
    row = await session.get(PlatformAccount, target_id)
    if row is None:
        raise NotFoundError("监控目标不存在")
    return {"message": "目标资料会在下一次统一采集时刷新", "target": _target(row)}


@router.post("/targets/actions/refresh-all", summary="刷新全部目标资料")
async def refresh_all_targets(_: AdminDep):
    return {"message": "全部目标资料会在下一次统一采集时刷新"}


@router.get("/credentials", summary="登录凭据")
async def list_credentials(session: SessionDep, _: AdminDep, platform: Platform | None = Query(None)):
    stmt = select(LoginCredential)
    if platform:
        stmt = stmt.where(LoginCredential.platform == platform.value)
    rows = (await session.execute(stmt.order_by(LoginCredential.id))).scalars().all()
    return {"items": [_credential(row) for row in rows]}


@router.post("/credentials/{credential_id}/toggle", summary="启用/停用凭据")
async def toggle_credential(credential_id: int, session: SessionDep, _: AdminDep):
    row = await session.get(LoginCredential, credential_id)
    if row is None:
        raise NotFoundError("凭据不存在")
    row.is_enabled = not row.is_enabled
    await session.commit()
    return _credential(row)


@router.get("/all", summary="设置页账号总览")
async def list_all_accounts(session: SessionDep, _: AdminDep):
    targets = (await session.execute(select(PlatformAccount).order_by(PlatformAccount.id))).scalars().all()
    credentials = (await session.execute(select(LoginCredential).order_by(LoginCredential.id))).scalars().all()
    return {"items": [{**_target(row), "is_target": 1} for row in targets] + [{**_credential(row), "is_target": 0} for row in credentials]}


@router.delete("/credentials/{credential_id}", summary="删除登录凭据")
async def delete_credential(credential_id: int, session: SessionDep, _: AdminDep):
    row = await session.get(LoginCredential, credential_id)
    if row is None:
        raise NotFoundError("凭据不存在")
    await session.delete(row)
    await session.commit()
    return {"ok": True}


def _target(row: PlatformAccount) -> dict:
    return {
        "id": row.id,
        "platform": row.platform,
        "account_id": row.account_id,
        "platform_uid": row.platform_uid,
        "nickname": row.nickname,
        "avatar_url": row.avatar_url,
        "is_enabled": row.is_enabled,
        "last_crawled_at": row.last_crawled_at,
        "status": "active" if row.is_enabled else "disabled",
        "is_target": 1,
    }


def _credential(row: LoginCredential) -> dict:
    return {
        "id": row.id,
        "platform": row.platform,
        "account_id": row.account_label,
        "nickname": row.nickname,
        "status": row.status,
        "is_enabled": row.is_enabled,
        "failure_count": row.failure_count,
        "last_failure_reason": row.last_failure_reason,
        "last_validated_at": row.last_validated_at,
        "usable": row.is_usable,
        "is_target": 0,
        "is_in_cooldown": bool(row.cooldown_until),
        "cookie_last_validated_at": row.last_validated_at,
    }

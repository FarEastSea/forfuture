"""登录凭据风控状态机。"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EventType, event_bus
from app.core.settings_store import settings_store
from app.domain.enums import CredentialStatus
from app.domain.identity import LoginCredential


def is_in_cooldown(credential: LoginCredential, now: datetime | None = None) -> bool:
    if not credential.cooldown_until:
        return False
    return credential.cooldown_until > (now or datetime.now())


def is_usable(credential: LoginCredential, *, allow_degraded: bool = False, now: datetime | None = None) -> bool:
    if not credential.is_enabled:
        return False
    allowed = {CredentialStatus.ACTIVE.value}
    if allow_degraded:
        allowed.add(CredentialStatus.DEGRADED.value)
    if credential.status not in allowed:
        return False
    return not is_in_cooldown(credential, now=now)


async def mark_verified(session: AsyncSession, credential: LoginCredential, *, refreshed: bool = False) -> None:
    now = datetime.now()
    credential.status = CredentialStatus.ACTIVE.value
    credential.last_validated_at = now
    if refreshed:
        credential.last_refreshed_at = now
    credential.failure_count = 0
    credential.last_failure_at = None
    credential.last_failure_reason = None
    credential.cooldown_until = None
    await event_bus.publish(
        EventType.CREDENTIAL_STATUS_CHANGED,
        {"id": credential.id, "status": credential.status},
    )


async def mark_failure(
    session: AsyncSession,
    credential: LoginCredential,
    *,
    reason: str,
    fatal: bool = False,
) -> str:
    cfg = await settings_store.load_all(session)
    now = datetime.now()
    if credential.status == CredentialStatus.DISABLED.value:
        return credential.status
    credential.failure_count = int(credential.failure_count or 0) + 1
    credential.last_failure_at = now
    credential.last_failure_reason = reason[:500]
    cooldown = int(cfg.get("credential_cooldown_minutes") or 60)
    if cooldown > 0:
        credential.cooldown_until = now + timedelta(minutes=cooldown)
    threshold = max(int(cfg.get("credential_failure_threshold") or 3), 1)
    if fatal or credential.failure_count >= threshold:
        credential.status = CredentialStatus.RELOGIN_PENDING.value
    else:
        credential.status = CredentialStatus.DEGRADED.value
    await event_bus.publish(
        EventType.CREDENTIAL_STATUS_CHANGED,
        {"id": credential.id, "status": credential.status, "reason": reason},
    )
    return credential.status


async def mark_expired(session: AsyncSession, credential: LoginCredential, *, reason: str) -> None:
    now = datetime.now()
    credential.status = CredentialStatus.EXPIRED.value
    credential.failure_count = int(credential.failure_count or 0) + 1
    credential.last_failure_at = now
    credential.last_failure_reason = reason[:500]
    credential.cooldown_until = None
    await event_bus.publish(
        EventType.CREDENTIAL_STATUS_CHANGED,
        {"id": credential.id, "status": credential.status, "reason": reason},
    )


async def pick_credential(
    session: AsyncSession,
    platform: str,
    *,
    preferred_id: int | None = None,
    allow_degraded: bool = False,
) -> LoginCredential | None:
    if preferred_id:
        cred = await session.get(LoginCredential, preferred_id)
        if cred and cred.platform == platform and is_usable(cred, allow_degraded=allow_degraded):
            return cred
    rows = (
        (
            await session.execute(
                select(LoginCredential)
                .where(LoginCredential.platform == platform, LoginCredential.is_enabled.is_(True))
                .order_by(LoginCredential.last_used_at.desc().nullslast(), LoginCredential.id.desc())
            )
        )
        .scalars()
        .all()
    )
    for cred in rows:
        if is_usable(cred, allow_degraded=allow_degraded):
            return cred
    return None

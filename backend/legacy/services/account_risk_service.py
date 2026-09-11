from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from legacy.models.account import Account
from legacy.models.system_config import SystemConfig


ACTIVE_STATUS = "active"
DEGRADED_STATUS = "degraded"
RELOGIN_PENDING_STATUS = "relogin_pending"
EXPIRED_STATUS = "expired"
DISABLED_STATUS = "disabled"

RISK_CONTROL_KEYS = {
    "skip_crawl_when_login_degraded",
    "cookie_failure_threshold",
    "risk_cooldown_minutes",
}


@dataclass(frozen=True)
class RiskControlConfig:
    skip_crawl_when_login_degraded: bool = True
    cookie_failure_threshold: int = 2
    risk_cooldown_minutes: int = 30


def normalize_account_status(status: str | None) -> str:
    normalized = (status or ACTIVE_STATUS).strip().lower()
    return normalized or ACTIVE_STATUS


def is_account_in_cooldown(account: Account, now: datetime | None = None) -> bool:
    if not account.risk_cooldown_until:
        return False

    current_time = now or datetime.now()
    return account.risk_cooldown_until > current_time


def is_login_account_eligible(
    account: Account,
    *,
    require_cookies: bool = False,
    allow_degraded: bool = False,
    now: datetime | None = None,
) -> bool:
    allowed_statuses = {ACTIVE_STATUS}
    if allow_degraded:
        allowed_statuses.add(DEGRADED_STATUS)

    if normalize_account_status(account.status) not in allowed_statuses:
        return False
    if require_cookies and not account.cookies:
        return False
    if is_account_in_cooldown(account, now=now):
        return False
    return True


def mark_account_verified(
    account: Account,
    *,
    now: datetime | None = None,
    refreshed: bool = False,
) -> None:
    current_time = now or datetime.now()
    account.status = ACTIVE_STATUS
    account.cookie_last_validated_at = current_time
    if refreshed:
        account.last_cookie_refresh_at = current_time
    account.failure_count = 0
    account.last_failure_at = None
    account.last_failure_reason = None
    account.risk_cooldown_until = None


def mark_account_failure(
    account: Account,
    *,
    reason: str,
    config: RiskControlConfig,
    now: datetime | None = None,
    relogin_required: bool = False,
) -> str:
    current_time = now or datetime.now()
    current_status = normalize_account_status(account.status)
    if current_status == DISABLED_STATUS:
        return current_status

    next_failure_count = int(account.failure_count or 0) + 1
    account.failure_count = next_failure_count
    account.last_failure_at = current_time
    account.last_failure_reason = reason[:500]
    if config.risk_cooldown_minutes > 0:
        account.risk_cooldown_until = current_time + timedelta(minutes=config.risk_cooldown_minutes)

    threshold = max(int(config.cookie_failure_threshold or 0), 1)
    if relogin_required or next_failure_count >= threshold:
        account.status = RELOGIN_PENDING_STATUS
    else:
        account.status = DEGRADED_STATUS

    return normalize_account_status(account.status)


def mark_account_expired(
    account: Account,
    *,
    reason: str,
    now: datetime | None = None,
) -> None:
    current_time = now or datetime.now()
    account.status = EXPIRED_STATUS
    account.failure_count = int(account.failure_count or 0) + 1
    account.last_failure_at = current_time
    account.last_failure_reason = reason[:500]
    account.risk_cooldown_until = None


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def load_risk_control_config(db: AsyncSession) -> RiskControlConfig:
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.key.in_(RISK_CONTROL_KEYS))
    )
    config_map = {item.key: item.value for item in result.scalars().all()}
    return RiskControlConfig(
        skip_crawl_when_login_degraded=_parse_bool(
            config_map.get("skip_crawl_when_login_degraded"),
            True,
        ),
        cookie_failure_threshold=max(
            _parse_int(config_map.get("cookie_failure_threshold"), 2),
            1,
        ),
        risk_cooldown_minutes=max(
            _parse_int(config_map.get("risk_cooldown_minutes"), 30),
            0,
        ),
    )


async def get_preferred_login_account(
    db: AsyncSession,
    platform: str,
    *,
    require_cookies: bool = False,
    allow_degraded: bool = False,
) -> Account | None:
    result = await db.execute(
        select(Account)
        .where(Account.platform == platform, Account.is_target == 0)
        .order_by(Account.last_login.desc(), Account.id.desc())
    )
    for account in result.scalars().all():
        if is_login_account_eligible(
            account,
            require_cookies=require_cookies,
            allow_degraded=allow_degraded,
        ):
            return account
    return None
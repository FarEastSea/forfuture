"""身份域：监控目标账号、登录凭据、浏览器指纹档案、代理。

监控目标（platform_accounts）与登录凭据（login_credentials）是两类完全不同的实体，
旧模型用 accounts.is_target 混在一张表里导致大量条件判断，这里彻底拆开。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.base import Base, TimestampMixin
from app.domain.enums import CredentialStatus


class Proxy(Base, TimestampMixin):
    """出口代理。未配置任何代理时系统直连，功能不受影响。"""

    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    scheme: Mapped[str] = mapped_column(String(20), nullable=False, default="http")
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    password: Mapped[str | None] = mapped_column(String(255))
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("host", "port", "username", name="uq_proxies_endpoint"),)

    @property
    def url(self) -> str:
        auth = ""
        if self.username:
            auth = self.username
            if self.password:
                auth = f"{auth}:{self.password}"
            auth = f"{auth}@"
        return f"{self.scheme}://{auth}{self.host}:{self.port}"


class BrowserProfile(Base, TimestampMixin):
    """浏览器指纹档案。

    一个凭据固定绑定一份档案：同一个账号每次都用同样的 UA、屏幕、时区、硬件参数和
    同一个 user-data-dir，指纹在时间维度上保持稳定，避免"同一账号每次指纹都不同"这种
    比暴露自动化更强的异常信号。
    """

    __tablename__ = "browser_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    platform: Mapped[str | None] = mapped_column(String(20))
    # 完整指纹描述：user_agent / platform / locale / timezone_id / viewport /
    # screen / hardware_concurrency / device_memory / color_scheme 等
    fingerprint: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    profile_dir: Mapped[str] = mapped_column(String(500), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)


class PlatformAccount(Base, TimestampMixin):
    """被监控的目标账号。"""

    __tablename__ = "platform_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    # 用户可见 ID：QQ 号 / 小红书号
    account_id: Mapped[str] = mapped_column(String(100), nullable=False)
    # 平台 API 使用的 ID：QQ 同 account_id；小红书是 ObjectId 形式的 user_id
    platform_uid: Mapped[str | None] = mapped_column(String(200))
    nickname: Mapped[str | None] = mapped_column(String(200))
    signature: Mapped[str | None] = mapped_column(Text)
    avatar_media_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL")
    )
    avatar_url: Mapped[str | None] = mapped_column(String(1000))
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    profile_history: Mapped[list["AccountProfileHistory"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("platform", "account_id", name="uq_platform_accounts_platform_account_id"),
        Index("ix_platform_accounts_platform_enabled", "platform", "is_enabled"),
    )


class AccountProfileHistory(Base):
    """目标账号的昵称/头像/签名变更历史。

    旧模型用 accounts.avatar_history / nickname_history 两个 JSON 列存，无法查询也无法索引，
    这里拆成行。
    """

    __tablename__ = "account_profile_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("platform_accounts.id", ondelete="CASCADE"), nullable=False
    )
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    account: Mapped[PlatformAccount] = relationship(back_populates="profile_history")

    __table_args__ = (Index("ix_account_profile_history_account_field", "account_id", "field"),)


class LoginCredential(Base, TimestampMixin):
    """用于采集的登录凭据，携带完整风控状态机。"""

    __tablename__ = "login_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    account_label: Mapped[str] = mapped_column(String(100), nullable=False)
    platform_uid: Mapped[str | None] = mapped_column(String(200))
    nickname: Mapped[str | None] = mapped_column(String(200))

    cookies: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    storage_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    token: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=CredentialStatus.ACTIVE.value
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_failure_reason: Mapped[str | None] = mapped_column(Text)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)

    browser_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("browser_profiles.id", ondelete="SET NULL")
    )
    proxy_id: Mapped[int | None] = mapped_column(ForeignKey("proxies.id", ondelete="SET NULL"))

    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    browser_profile: Mapped[BrowserProfile | None] = relationship(lazy="selectin")
    proxy: Mapped[Proxy | None] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint(
            "platform", "account_label", name="uq_login_credentials_platform_account_label"
        ),
        Index("ix_login_credentials_platform_status", "platform", "status"),
    )

    @property
    def is_usable(self) -> bool:
        return self.is_enabled and self.status in {
            CredentialStatus.ACTIVE.value,
            CredentialStatus.DEGRADED.value,
        }

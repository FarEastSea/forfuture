from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, func
from app.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=False)  # qq / xhs
    account_id = Column(String(100), nullable=False)  # 用户可见ID: QQ号 / 红薯号
    platform_uid = Column(String(200))  # 平台API用ID: QQ同account_id / XHS为ObjectId
    nickname = Column(String(100))
    avatar_url = Column(String(500))
    signature = Column(String(500))  # 个性签名
    avatar_history = Column(JSON, default=list)  # 头像历史记录
    nickname_history = Column(JSON, default=list)  # 昵称历史记录 [{nickname, time}]
    cookies = Column(Text)
    token = Column(Text)
    status = Column(String(20), default="active")
    last_login = Column(DateTime)
    cookie_last_validated_at = Column(DateTime)
    last_cookie_refresh_at = Column(DateTime)
    last_failure_at = Column(DateTime)
    last_failure_reason = Column(String(500))
    risk_cooldown_until = Column(DateTime)
    failure_count = Column(Integer, nullable=False, default=0)
    is_target = Column(Integer, default=0)  # 1=被监控目标账号
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        {"schema": None},
    )

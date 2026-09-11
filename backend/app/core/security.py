"""管理员认证：HTTP 与 WebSocket 共用一套令牌校验。

优先用 ADMIN_API_TOKEN；未配置时才回退到 SECRET_KEY，且 SECRET_KEY 必须不是默认值——
否则视为"没有配置管理员令牌"，直接拒绝，而不是放行。
"""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, WebSocket, status

from app.core.config import settings

ADMIN_WS_SUBPROTOCOL_PREFIX = "admin-token."


def _token_matches(candidate: str) -> bool:
    expected = settings.effective_admin_token
    if not expected:
        return False
    return secrets.compare_digest(candidate.strip(), expected)


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer":
        return value.strip()
    return authorization.strip()


async def require_admin(
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """FastAPI 依赖：校验管理员令牌。"""
    if not settings.has_strong_admin_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="管理员令牌未配置：请设置 ADMIN_API_TOKEN 或把 SECRET_KEY 改成非默认值",
        )

    for candidate in (_extract_bearer(authorization), (x_admin_token or "").strip()):
        if candidate and _token_matches(candidate):
            return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="管理员令牌无效",
        headers={"WWW-Authenticate": "Bearer"},
    )


def extract_ws_token(websocket: WebSocket) -> str:
    """从 WebSocket 握手中取令牌。

    浏览器的 WebSocket API 不能自定义请求头，因此令牌通过子协议传递：
    子协议名形如 admin-token.<token>。
    """
    header_token = _extract_bearer(websocket.headers.get("authorization"))
    if header_token:
        return header_token

    admin_header = websocket.headers.get("x-admin-token")
    if admin_header:
        return admin_header.strip()

    raw = websocket.headers.get("sec-websocket-protocol", "")
    for entry in (part.strip() for part in raw.split(",")):
        if entry.startswith(ADMIN_WS_SUBPROTOCOL_PREFIX):
            return entry[len(ADMIN_WS_SUBPROTOCOL_PREFIX) :]
    return ""


def ws_subprotocol_for(websocket: WebSocket) -> str | None:
    """握手成功时需要原样回显客户端提供的子协议。"""
    raw = websocket.headers.get("sec-websocket-protocol", "")
    for entry in (part.strip() for part in raw.split(",")):
        if entry.startswith(ADMIN_WS_SUBPROTOCOL_PREFIX):
            return entry
    return None


async def authorize_websocket(websocket: WebSocket) -> bool:
    """校验并接受 WebSocket 连接；失败时关闭并返回 False。"""
    if not settings.has_strong_admin_token or not _token_matches(extract_ws_token(websocket)):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return False
    await websocket.accept(subprotocol=ws_subprotocol_for(websocket))
    return True

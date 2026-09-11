from __future__ import annotations

import base64
from secrets import compare_digest

from dotenv import dotenv_values
from fastapi import Header, HTTPException, WebSocket, WebSocketException, status

from legacy.config import ENV_FILE, settings


ADMIN_WS_PROTOCOL = "airr-admin"
ADMIN_WS_TOKEN_PREFIX = "airr-admin-token."
DEFAULT_SECRET_KEY = "change-me-to-a-random-string"


def _normalize_token(token: str | None) -> str:
    return token.strip() if token else ""


def _extract_bearer_token(authorization: str | None) -> str:
    normalized = _normalize_token(authorization)
    if not normalized:
        return ""

    scheme, _, value = normalized.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return _normalize_token(value)


def _verify_token(expected: str, provided: str | None) -> bool:
    return compare_digest(_normalize_token(provided), _normalize_token(expected))


def _require_configured_http_token(raw_token: str | None, detail: str) -> str:
    normalized = _normalize_token(raw_token)
    if normalized:
        return normalized
    raise HTTPException(status_code=503, detail=detail)


def _require_configured_ws_token(raw_token: str | None, detail: str) -> str:
    normalized = _normalize_token(raw_token)
    if normalized:
        return normalized
    raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason=detail)


def admin_auth_enabled() -> bool:
    return bool(_get_effective_admin_token())


def _get_runtime_env_value(*keys: str) -> str:
    if ENV_FILE.exists():
        try:
            env_values = dotenv_values(ENV_FILE)
        except Exception:
            env_values = {}
        for key in keys:
            value = _normalize_token(env_values.get(key))
            if value:
                return value

    for key in keys:
        settings_key = key.lower()
        value = _normalize_token(getattr(settings, settings_key, ""))
        if value:
            return value

    return ""


def _get_effective_admin_token() -> str:
    configured_admin_token = _get_runtime_env_value("ADMIN_API_TOKEN")
    if configured_admin_token:
        return configured_admin_token

    fallback_secret = _get_runtime_env_value("SECRET_KEY")
    if fallback_secret and fallback_secret != DEFAULT_SECRET_KEY:
        return fallback_secret

    return ""


async def require_admin_http(
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    expected_token = _get_effective_admin_token()
    if not expected_token:
        raise HTTPException(status_code=503, detail="管理员令牌未配置")

    provided_token = (
        _extract_bearer_token(authorization)
        or _normalize_token(x_admin_token)
    )
    if _verify_token(expected_token, provided_token):
        return

    raise HTTPException(
        status_code=401,
        detail="管理员认证失败",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode_ws_protocol_token(protocol: str) -> str:
    if not protocol.startswith(ADMIN_WS_TOKEN_PREFIX):
        return ""

    encoded_token = protocol[len(ADMIN_WS_TOKEN_PREFIX):]
    if not encoded_token:
        return ""

    padding = "=" * (-len(encoded_token) % 4)
    try:
        return base64.urlsafe_b64decode(f"{encoded_token}{padding}").decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""


def _get_ws_protocols(websocket: WebSocket) -> list[str]:
    raw_protocols = websocket.headers.get("sec-websocket-protocol", "")
    return [protocol.strip() for protocol in raw_protocols.split(",") if protocol.strip()]


def _get_ws_token(websocket: WebSocket) -> str:
    for protocol in _get_ws_protocols(websocket):
        decoded_token = _decode_ws_protocol_token(protocol)
        if decoded_token:
            return decoded_token

    return (
        _extract_bearer_token(websocket.headers.get("authorization"))
        or _normalize_token(websocket.headers.get("x-admin-token"))
    )


def get_admin_ws_accept_subprotocol(websocket: WebSocket) -> str | None:
    return ADMIN_WS_PROTOCOL if ADMIN_WS_PROTOCOL in _get_ws_protocols(websocket) else None


async def require_admin_websocket(websocket: WebSocket):
    expected_token = _get_effective_admin_token()
    if not expected_token:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="管理员令牌未配置")

    if _verify_token(expected_token, _get_ws_token(websocket)):
        return

    raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="管理员认证失败")


async def require_napcat_websocket(websocket: WebSocket):
    expected_token = _get_runtime_env_value("NAPCAT_TOKEN") or _get_effective_admin_token()
    if not expected_token:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="NapCat 令牌未配置")

    provided_token = _normalize_token(websocket.headers.get("x-napcat-token"))
    if _verify_token(expected_token, provided_token):
        return

    raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="NapCat 认证失败")
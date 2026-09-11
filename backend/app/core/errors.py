"""统一异常体系。

采集链路上的失败必须能区分"这次不行"和"这个凭据废了"，风控状态机依赖这个区分。
"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    """所有业务异常的基类。"""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, detail: Any = None):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationError(AppError):
    status_code = 400
    code = "validation_error"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ConfigurationError(AppError):
    status_code = 500
    code = "configuration_error"


class CrawlError(AppError):
    """采集失败的基类。

    retryable 决定是否值得重试；credential_fatal 决定是否要把凭据打成需要重新登录。
    """

    status_code = 502
    code = "crawl_error"
    retryable = True
    credential_fatal = False


class TransientCrawlError(CrawlError):
    """网络抖动、超时之类，重试即可。"""

    code = "crawl_transient"
    retryable = True


class RateLimitedError(CrawlError):
    """被限流。需要退避后再试，不代表凭据失效。"""

    code = "crawl_rate_limited"
    retryable = True

    def __init__(self, message: str, *, retry_after_seconds: float | None = None, detail: Any = None):
        super().__init__(message, detail=detail)
        self.retry_after_seconds = retry_after_seconds


class AntiBotError(CrawlError):
    """触发风控（验证码、滑块、异常页面）。当次不可重试，需要人工或换出口。"""

    code = "crawl_anti_bot"
    retryable = False


class CredentialExpiredError(CrawlError):
    """Cookie 失效 / 需要重新登录。"""

    code = "credential_expired"
    retryable = False
    credential_fatal = True


class CredentialUnavailableError(AppError):
    """没有可用的登录凭据（全部处于冷却/待重登/停用）。"""

    status_code = 409
    code = "credential_unavailable"


class PlatformNotSupportedError(AppError):
    status_code = 400
    code = "platform_not_supported"

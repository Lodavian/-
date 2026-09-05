from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SafeConnectionError:
    code: str
    summary: str
    retryable: bool
    detail: str


def classify_connection_error(exc: BaseException, *, secrets: tuple[str, ...] = ()) -> SafeConnectionError:
    """Turn SDK/network failures into stable UI-safe connection errors."""
    raw = str(exc or "").strip()
    lowered = raw.lower()
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)) or any(
        word in lowered for word in ("timeout", "timed out", "network", "dns", "connection")
    ):
        code, summary, retryable = "NETWORK_UNAVAILABLE", "券商API网络连接不可用，请稍后重试", True
    elif any(word in lowered for word in ("unauthorized", "invalid_session", "invalid credential", "鉴权", "凭据")):
        code, summary, retryable = "AUTH_INVALID", "App Key 或 App Secret 无效", False
    elif any(word in lowered for word in ("permission", "forbidden", "权限")):
        code, summary, retryable = "PERMISSION_DENIED", "OpenAPI 应用缺少所需账户或交易权限", False
    elif any(word in lowered for word in ("没有可用交易账户", "no account")):
        code, summary, retryable = "NO_ACCOUNTS", "该 OpenAPI 应用下没有可用交易账户", False
    else:
        code, summary, retryable = "CONNECT_FAILED", "券商API账户连接失败", True

    detail = raw or exc.__class__.__name__
    for secret in secrets:
        if secret:
            detail = detail.replace(secret, "[REDACTED]")
    detail = re.sub(
        r"(?i)(app[_ -]?secret|access[_ -]?token|authorization|signature)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        detail,
    )
    detail = detail[:800]
    return SafeConnectionError(code=code, summary=summary, retryable=retryable, detail=detail)

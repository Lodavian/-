from __future__ import annotations

import os
import math
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> None:
    """Load a small, predictable subset of dotenv syntax without another dependency."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key and key.replace("_", "").isalnum():
            os.environ.setdefault(key, value)


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean (true/false)")


def _int_env(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return value


def _required_int_env(name: str, *, minimum: int = 1, maximum: int | None = None) -> int:
    if not os.environ.get(name, "").strip():
        raise ValueError(f"LIVE 模式必须配置 {name}")
    return _int_env(name, 0, minimum=minimum, maximum=maximum)


def _float_env(
    name: str,
    default: float,
    *,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value <= minimum:
        raise ValueError(f"{name} must be greater than {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return value


def _required_float_env(name: str, *, minimum: float = 0.0, maximum: float | None = None) -> float:
    if not os.environ.get(name, "").strip():
        raise ValueError(f"LIVE 模式必须配置 {name}")
    return _float_env(name, 0.0, minimum=minimum, maximum=maximum)


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    app_key: str
    app_secret: str
    account_id: str
    account_hint: str
    sandbox: bool
    default_symbol: str
    max_order_qty: int
    max_order_value: float
    quote_stale_seconds: float
    port: int
    # These defaults keep lightweight/sandbox unit fixtures backwards compatible.
    # LIVE load_settings() replaces them with explicitly configured values.
    live_allowed: bool = False
    max_market_qty: int = 100
    max_market_estimated_value: float = 5_000.0
    max_daily_gross_notional: float = 25_000.0
    max_open_orders: int = 1
    sdk_connect_timeout: float = 10.0
    sdk_read_timeout: float = 15.0
    sdk_total_timeout: float = 20.0

    @property
    def token_dir(self) -> Path:
        return self.project_root / "runtime" / "webull_tokens"

    @property
    def api_host(self) -> str:
        return "api.sandbox.webull.hk" if self.sandbox else "api.webull.hk"

    @property
    def data_host(self) -> str:
        return "data-api.sandbox.webull.hk" if self.sandbox else "data-api.webull.hk"

    def require_credentials(self) -> None:
        if not self.app_key or not self.app_secret:
            raise ValueError("请先绑定券商API账户凭据（缺少 APP_KEY 或 APP_SECRET）")


def load_settings(project_root: Path | None = None) -> Settings:
    root = (project_root or PROJECT_ROOT).resolve()
    load_env_file(root / ".env")
    sandbox = _bool_env("FAST_ORDER_SANDBOX", True)
    live_allowed = _bool_env("FAST_ORDER_LIVE_ALLOWED", False)

    if not sandbox:
        if not live_allowed:
            raise ValueError("LIVE 模式必须配置 FAST_ORDER_LIVE_ALLOWED=true")
        max_market_qty = _required_int_env("FAST_ORDER_MAX_MARKET_QTY", maximum=1_000_000)
        max_market_estimated_value = _required_float_env(
            "FAST_ORDER_MAX_MARKET_ESTIMATED_VALUE", maximum=1_000_000_000
        )
        max_daily_gross_notional = _required_float_env(
            "FAST_ORDER_MAX_DAILY_GROSS_NOTIONAL", maximum=10_000_000_000
        )
        max_open_orders = _required_int_env("FAST_ORDER_MAX_OPEN_ORDERS", maximum=10_000)
    else:
        max_market_qty = _int_env("FAST_ORDER_MAX_MARKET_QTY", 100, maximum=1_000_000)
        max_market_estimated_value = _float_env(
            "FAST_ORDER_MAX_MARKET_ESTIMATED_VALUE", 5_000, maximum=1_000_000_000
        )
        max_daily_gross_notional = _float_env(
            "FAST_ORDER_MAX_DAILY_GROSS_NOTIONAL", 25_000, maximum=10_000_000_000
        )
        max_open_orders = _int_env("FAST_ORDER_MAX_OPEN_ORDERS", 1, maximum=10_000)

    return Settings(
        project_root=root,
        app_key=os.environ.get("FAST_ORDER_WEBULL_APP_KEY", "").strip(),
        app_secret=os.environ.get("FAST_ORDER_WEBULL_APP_SECRET", "").strip(),
        account_id=os.environ.get("FAST_ORDER_WEBULL_ACCOUNT_ID", "").strip(),
        account_hint=os.environ.get("FAST_ORDER_WEBULL_ACCOUNT_HINT", "").strip().upper(),
        sandbox=sandbox,
        default_symbol=os.environ.get("FAST_ORDER_DEFAULT_SYMBOL", "AAPL").strip().upper() or "AAPL",
        max_order_qty=_int_env("FAST_ORDER_MAX_ORDER_QTY", 1000, maximum=1_000_000),
        max_order_value=_float_env("FAST_ORDER_MAX_ORDER_VALUE", 25_000, maximum=1_000_000_000),
        quote_stale_seconds=_float_env("FAST_ORDER_QUOTE_STALE_SECONDS", 8, maximum=300),
        port=_int_env("FAST_ORDER_PORT", 8765, minimum=1024, maximum=65535),
        live_allowed=live_allowed,
        max_market_qty=max_market_qty,
        max_market_estimated_value=max_market_estimated_value,
        max_daily_gross_notional=max_daily_gross_notional,
        max_open_orders=max_open_orders,
        sdk_connect_timeout=_float_env("FAST_ORDER_SDK_CONNECT_TIMEOUT", 10, maximum=300),
        sdk_read_timeout=_float_env("FAST_ORDER_SDK_READ_TIMEOUT", 15, maximum=300),
        sdk_total_timeout=_float_env("FAST_ORDER_SDK_TOTAL_TIMEOUT", 20, maximum=600),
    )

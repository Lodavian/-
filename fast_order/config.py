from __future__ import annotations

import os
import math
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .paths import AppPaths, PathResolutionError


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
    paths: AppPaths | None = None

    @property
    def app_paths(self) -> AppPaths:
        return self.paths or AppPaths.resolve(self.project_root, self.project_root, development=True)

    @property
    def resource_root(self) -> Path:
        return self.app_paths.resource_root

    @property
    def data_root(self) -> Path:
        return self.app_paths.data_root

    @property
    def token_dir(self) -> Path:
        return self.app_paths.broker_tokens

    @property
    def profile_path(self) -> Path:
        return self.app_paths.account_profile

    @property
    def logs_dir(self) -> Path:
        return self.app_paths.logs

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
    resource_root = (project_root or PROJECT_ROOT).resolve()
    load_env_file(resource_root / ".env")
    try:
        paths = AppPaths.resolve(resource_root)
    except PathResolutionError as exc:
        raise ValueError(str(exc)) from exc
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
        # ``project_root`` is retained as the writable data root for existing
        # callers. Read-only resources are available through ``paths``.
        project_root=paths.data_root,
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
        paths=paths,
    )


_PRODUCTION_FIELDS = frozenset(
    {
        "schemaVersion",
        "productVersion",
        "environment",
        "liveAllowed",
        "defaultSymbol",
        "port",
        "maxOrderQty",
        "maxOrderValue",
        "maxMarketQty",
        "maxMarketEstimatedValue",
        "maxDailyGrossNotional",
        "maxOpenOrders",
        "quoteStaleSeconds",
        "sdkConnectTimeout",
        "sdkReadTimeout",
        "sdkTotalTimeout",
    }
)


def load_production_settings(paths: AppPaths) -> Settings:
    """Load the immutable runtime policy from ``config/production.json``.

    Production deliberately has a separate loader from :func:`load_settings`:
    it never reads ``.env`` or credential environment variables.  Credentials
    are supplied by the account profile/token store after this policy has been
    loaded by the bootstrap.
    """
    config_path = paths.resource_root / "config" / "production.json"
    if not config_path.is_file():
        raise ValueError("生产配置文件缺失")
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("生产配置文件无效") from exc
    if not isinstance(value, dict):
        raise ValueError("生产配置根节点必须是对象")
    if set(value) != _PRODUCTION_FIELDS:
        raise ValueError("生产配置字段不完整或包含未知字段")

    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise ValueError("生产配置 schemaVersion 不受支持")
    product_version = value["productVersion"]
    if product_version != "1.0.0":
        raise ValueError("生产配置 productVersion 不受支持")
    environment = value["environment"]
    if environment not in {"SANDBOX", "LIVE"}:
        raise ValueError("生产配置 environment 无效")
    if type(value["liveAllowed"]) is not bool:
        raise ValueError("生产配置 liveAllowed 无效")
    if environment == "LIVE" and not value["liveAllowed"]:
        raise ValueError("LIVE 生产配置必须启用 liveAllowed")

    default_symbol = value["defaultSymbol"]
    if not isinstance(default_symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,15}", default_symbol):
        raise ValueError("生产配置 defaultSymbol 无效")

    def production_int(key: str, minimum: int, maximum: int) -> int:
        number = value[key]
        if type(number) is not int or not minimum <= number <= maximum:
            raise ValueError(f"生产配置 {key} 无效")
        return number

    def production_float(key: str, minimum: float, maximum: float) -> float:
        number = value[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValueError(f"生产配置 {key} 无效")
        result = float(number)
        if not math.isfinite(result) or not minimum < result <= maximum:
            raise ValueError(f"生产配置 {key} 无效")
        return result

    port = production_int("port", 1024, 65535)
    max_order_qty = production_int("maxOrderQty", 1, 1_000_000)
    max_market_qty = production_int("maxMarketQty", 1, 1_000_000)
    max_open_orders = production_int("maxOpenOrders", 1, 10_000)
    max_order_value = production_float("maxOrderValue", 0, 1_000_000_000)
    max_market_value = production_float("maxMarketEstimatedValue", 0, 1_000_000_000)
    max_daily_notional = production_float("maxDailyGrossNotional", 0, 10_000_000_000)
    quote_stale = production_float("quoteStaleSeconds", 0, 300)
    sdk_connect = production_float("sdkConnectTimeout", 0, 300)
    sdk_read = production_float("sdkReadTimeout", 0, 300)
    sdk_total = production_float("sdkTotalTimeout", 0, 600)
    if max_market_qty > max_order_qty or max_market_value > max_order_value:
        raise ValueError("生产配置市价单上限不得超过通用订单上限")
    if sdk_total < max(sdk_connect, sdk_read):
        raise ValueError("生产配置总超时不得短于连接或读取超时")

    return Settings(
        project_root=paths.data_root,
        app_key="",
        app_secret="",
        account_id="",
        account_hint="",
        sandbox=environment == "SANDBOX",
        default_symbol=default_symbol,
        max_order_qty=max_order_qty,
        max_order_value=max_order_value,
        quote_stale_seconds=quote_stale,
        port=port,
        live_allowed=value["liveAllowed"],
        max_market_qty=max_market_qty,
        max_market_estimated_value=max_market_value,
        max_daily_gross_notional=max_daily_notional,
        max_open_orders=max_open_orders,
        sdk_connect_timeout=sdk_connect,
        sdk_read_timeout=sdk_read,
        sdk_total_timeout=sdk_total,
        paths=paths,
    )

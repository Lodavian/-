from __future__ import annotations

from pathlib import Path

import pytest

from fast_order.config import load_settings


CONFIG_KEYS = (
    "FAST_ORDER_SANDBOX",
    "FAST_ORDER_LIVE_ALLOWED",
    "FAST_ORDER_MAX_MARKET_QTY",
    "FAST_ORDER_MAX_MARKET_ESTIMATED_VALUE",
    "FAST_ORDER_MAX_DAILY_GROSS_NOTIONAL",
    "FAST_ORDER_MAX_OPEN_ORDERS",
    "FAST_ORDER_QUOTE_STALE_SECONDS",
    "FAST_ORDER_PORT",
    "FAST_ORDER_SDK_CONNECT_TIMEOUT",
    "FAST_ORDER_SDK_READ_TIMEOUT",
    "FAST_ORDER_SDK_TOTAL_TIMEOUT",
)


def clean_config(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_sandbox_is_safe_default_and_has_explicit_live_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clean_config(monkeypatch)
    settings = load_settings(tmp_path)
    assert settings.sandbox is True
    assert settings.live_allowed is False
    assert settings.max_market_qty == 100
    assert settings.max_open_orders == 1


def test_boolean_values_are_strict_and_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clean_config(monkeypatch)
    monkeypatch.setenv("FAST_ORDER_SANDBOX", "definitely-not-boolean")
    with pytest.raises(ValueError, match="FAST_ORDER_SANDBOX"):
        load_settings(tmp_path)


def test_live_requires_explicit_opt_in_and_all_risk_caps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clean_config(monkeypatch)
    monkeypatch.setenv("FAST_ORDER_SANDBOX", "false")
    with pytest.raises(ValueError, match="FAST_ORDER_LIVE_ALLOWED"):
        load_settings(tmp_path)

    monkeypatch.setenv("FAST_ORDER_LIVE_ALLOWED", "true")
    with pytest.raises(ValueError, match="FAST_ORDER_MAX_MARKET_QTY"):
        load_settings(tmp_path)


def test_live_loads_explicit_caps_and_deadlines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clean_config(monkeypatch)
    monkeypatch.setenv("FAST_ORDER_SANDBOX", "false")
    monkeypatch.setenv("FAST_ORDER_LIVE_ALLOWED", "true")
    monkeypatch.setenv("FAST_ORDER_MAX_MARKET_QTY", "25")
    monkeypatch.setenv("FAST_ORDER_MAX_MARKET_ESTIMATED_VALUE", "1250.5")
    monkeypatch.setenv("FAST_ORDER_MAX_DAILY_GROSS_NOTIONAL", "10000")
    monkeypatch.setenv("FAST_ORDER_MAX_OPEN_ORDERS", "2")
    monkeypatch.setenv("FAST_ORDER_SDK_CONNECT_TIMEOUT", "7.5")
    monkeypatch.setenv("FAST_ORDER_SDK_READ_TIMEOUT", "8")
    monkeypatch.setenv("FAST_ORDER_SDK_TOTAL_TIMEOUT", "12")
    settings = load_settings(tmp_path)
    assert settings.sandbox is False
    assert settings.live_allowed is True
    assert settings.max_market_qty == 25
    assert settings.max_market_estimated_value == 1250.5
    assert settings.max_daily_gross_notional == 10000
    assert settings.max_open_orders == 2
    assert settings.sdk_connect_timeout == 7.5
    assert settings.sdk_read_timeout == 8
    assert settings.sdk_total_timeout == 12


@pytest.mark.parametrize("key,value", [
    ("FAST_ORDER_QUOTE_STALE_SECONDS", "nan"),
    ("FAST_ORDER_SDK_CONNECT_TIMEOUT", "Infinity"),
    ("FAST_ORDER_SDK_TOTAL_TIMEOUT", "-Infinity"),
])
def test_non_finite_float_settings_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, value: str) -> None:
    clean_config(monkeypatch)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="finite"):
        load_settings(tmp_path)


@pytest.mark.parametrize("key,value", [
    ("FAST_ORDER_QUOTE_STALE_SECONDS", "0"),
    ("FAST_ORDER_PORT", "65536"),
    ("FAST_ORDER_SDK_READ_TIMEOUT", "301"),
])
def test_time_and_endpoint_settings_are_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, value: str) -> None:
    clean_config(monkeypatch)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        load_settings(tmp_path)

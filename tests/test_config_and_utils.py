from __future__ import annotations

from pathlib import Path

import pytest

from fast_order.config import load_env_file, load_settings
from fast_order.utils import available_position, normalize_symbol, protobuf_to_dict, redact, search_instruments, select_account_id


def test_env_file_does_not_override_process_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAST_ORDER_WEBULL_APP_KEY", "process-key")
    monkeypatch.setenv("FAST_ORDER_SANDBOX", "true")
    (tmp_path / ".env").write_text("FAST_ORDER_WEBULL_APP_KEY=file-key\nFAST_ORDER_SANDBOX=true\n", encoding="utf-8")
    settings = load_settings(tmp_path)
    assert settings.app_key == "process-key"
    assert settings.sandbox is True


def test_missing_credentials_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAST_ORDER_WEBULL_APP_KEY", raising=False)
    monkeypatch.delenv("FAST_ORDER_WEBULL_APP_SECRET", raising=False)
    settings = load_settings(tmp_path)
    with pytest.raises(ValueError, match="APP_KEY"):
        settings.require_credentials()


def test_account_selection_requires_explicit_id_for_multiple_accounts() -> None:
    accounts = [{"account_id": "one"}, {"account_id": "two"}]
    with pytest.raises(ValueError, match="多个账户"):
        select_account_id(accounts, "")
    assert select_account_id(accounts, "two") == "two"
    with pytest.raises(ValueError, match="不在"):
        select_account_id(accounts, "missing")


def test_account_selection_accepts_unique_masked_hint() -> None:
    accounts = [
        {"account_id": "VPH1234567NA"},
        {"account_id": "75G123456PCA"},
    ]
    assert select_account_id(accounts, "", "VPH***7NA") == "VPH1234567NA"
    with pytest.raises(ValueError, match="不匹配"):
        select_account_id(accounts, "", "ABC***XYZ")


def test_account_selection_rejects_ambiguous_masked_hint() -> None:
    accounts = [
        {"account_id": "VPH1111117NA"},
        {"account_id": "VPH2222227NA"},
    ]
    with pytest.raises(ValueError, match="匹配多个"):
        select_account_id(accounts, "", "VPH***7NA")


@pytest.mark.parametrize("value", ["", "AAPL!", "../../AAPL", "AAPL A", "1234"])
def test_symbol_validation_rejects_non_us_ticker_shapes(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_symbol(value)


def test_redaction_removes_credentials_recursively() -> None:
    value = {
        "app_secret": "secret",
        "nested": [{"access_token": "token", "symbol": "AAPL"}],
    }
    assert redact(value) == {
        "app_secret": "***",
        "nested": [{"access_token": "***", "symbol": "AAPL"}],
    }


def test_available_position_prefers_sellable_over_total_quantity() -> None:
    rows = [{"symbol": "AAPL", "quantity": "100", "available_quantity": "7"}]
    assert available_position(rows, "AAPL") == 7


def test_nested_stream_values_are_converted_recursively() -> None:
    class Message:
        def __init__(self) -> None:
            self.symbol = "AAPL"
            self.price = "101.5"

    assert protobuf_to_dict({"items": [Message()]}) == {
        "items": [{"symbol": "AAPL", "price": "101.5"}]
    }


def test_instrument_search_ranks_exact_tradable_before_retired_and_prefixes() -> None:
    rows = [
        {"symbol": "NCT", "name": "NEWCASTLE INVT HLDGS CORP", "exchange_code": "NYS", "status": "NT"},
        {"symbol": "NCTY", "name": "THE9 LIMITED", "exchange_code": "NSQ", "status": "OC"},
        {"symbol": "NCT", "name": "INTERCONT LIMITED", "exchange_code": "NSQ", "status": "OC"},
    ]
    results = search_instruments(rows, "nct")
    assert [(item["symbol"], item["status"]) for item in results] == [
        ("NCT", "OC"),
        ("NCT", "NT"),
        ("NCTY", "OC"),
    ]


def test_instrument_search_supports_company_name_and_limits_public_fields() -> None:
    rows = [{"symbol": "AAPL", "name": "APPLE INC", "exchange_code": "NSQ", "status": "OC", "secret": "no"}]
    assert search_instruments(rows, "apple") == [{
        "symbol": "AAPL",
        "name": "APPLE INC",
        "exchange": "NSQ",
        "status": "OC",
        "subCategory": "",
    }]

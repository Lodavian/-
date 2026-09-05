from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app as app_module
import fast_order.webull_gateway as gateway_module
from fast_order.connection_errors import classify_connection_error
from fast_order.service import TradingService
from fast_order.webull_gateway import WebullGateway
from tests.test_gateway import make_settings as make_gateway_settings
from tests.test_service import settings as make_service_settings


@pytest.mark.asyncio
async def test_gateway_rejects_credential_hot_swap_while_connected(tmp_path: Path) -> None:
    gateway = WebullGateway(make_gateway_settings(tmp_path), lambda _event: None)
    gateway.connected = True

    with pytest.raises(RuntimeError, match="先断开账户"):
        await gateway.discover_accounts("replacement-key", "replacement-secret")

    assert gateway._app_key == "key"
    assert gateway._app_secret == "secret"


@pytest.mark.asyncio
async def test_gateway_discovers_accounts_without_real_sdk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = WebullGateway(make_gateway_settings(tmp_path), lambda _event: None)
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "accounts": [
                {"account_id": "ACCOUNT-0001", "account_type": "CASH"},
                {"account_id": "ACCOUNT-0002", "account_type": "MARGIN"},
            ]
        },
    )

    def build_clients() -> None:
        gateway.trade_client = SimpleNamespace(
            account_v2=SimpleNamespace(get_account_list=lambda: response)
        )
        gateway.data_client = SimpleNamespace()

    monkeypatch.setattr(gateway_module, "harden_windows_acl", lambda _path: True)
    monkeypatch.setattr(gateway, "_build_trade_clients", build_clients)

    accounts = await gateway.discover_accounts("temporary-key", "temporary-secret")

    assert [row["account_id"] for row in accounts] == ["ACCOUNT-0001", "ACCOUNT-0002"]
    assert gateway._discovered_account_ids == ["ACCOUNT-0001", "ACCOUNT-0002"]
    assert gateway.connected is False
    assert gateway.streaming_client is None


@pytest.mark.asyncio
async def test_service_rejects_binding_while_connected(tmp_path: Path) -> None:
    class ConnectedGateway:
        connected = True
        stream_connected = True
        subscription_ready = True
        events_connected = True
        current_symbol = "AAPL"

    async def publish(_event: dict[str, Any]) -> None:
        return None

    service = TradingService(make_service_settings(tmp_path), ConnectedGateway(), publish)
    try:
        with pytest.raises(RuntimeError, match="先断开账户"):
            await service.begin_account_binding("replacement-key", "replacement-secret")
    finally:
        service.ledger.close()


def test_connection_readiness_concepts_remain_independent(tmp_path: Path) -> None:
    gateway = SimpleNamespace(
        connected=True,
        stream_connected=True,
        subscription_ready=False,
        events_connected=True,
        current_symbol="AAPL",
    )

    async def publish(_event: dict[str, Any]) -> None:
        return None

    service = TradingService(make_service_settings(tmp_path), gateway, publish)
    try:
        service.connection_status = "connected"
        service.set_credential_state("CREDENTIALS_SAVED")
        service.last_market_at = time.time()

        no_subscription = service.connection_event()
        assert no_subscription["credentialsSaved"] is True
        assert no_subscription["connectionState"] == "CONNECTED"
        assert no_subscription["marketReady"] is False
        assert no_subscription["orderEventsReady"] is True
        assert no_subscription["tradingReady"] is False

        gateway.subscription_ready = True
        gateway.events_connected = False
        no_order_events = service.connection_event()
        assert no_order_events["marketReady"] is True
        assert no_order_events["orderEventsReady"] is False
        assert no_order_events["tradingReady"] is False

        gateway.events_connected = True
        ready = service.connection_event()
        assert ready["marketReady"] is True
        assert ready["orderEventsReady"] is True
        assert ready["tradingReady"] is True
        assert ready["tradingEnabled"] is True
    finally:
        service.ledger.close()


@pytest.mark.asyncio
async def test_binding_candidates_expose_only_opaque_refs_and_masks() -> None:
    full_ids = ("ACCOUNT-FULL-0001", "ACCOUNT-FULL-0002")
    app_secret = "secret-that-must-not-leak"
    messages: list[dict[str, Any]] = []

    class BindingService:
        async def begin_account_binding(self, _app_key: str, _app_secret: str) -> list[dict[str, str]]:
            return [
                {"account_id": full_ids[0], "account_type": "CASH"},
                {"account_id": full_ids[1], "account_type": "MARGIN"},
            ]

    application = app_module.FastOrderApplication.__new__(app_module.FastOrderApplication)
    application.service = BindingService()
    application.gateway = SimpleNamespace(account_id="")
    application._binding_sessions = {}
    application._connection_errors = {}

    async def capture(_ws: object, payload: dict[str, Any]) -> bool:
        messages.append(payload)
        return True

    application._send_json = capture
    await application._handle_binding_start(
        object(),
        {
            "appKey": "temporary-key",
            "appSecret": app_secret,
            "rememberCredentials": False,
            "autoConnect": False,
        },
    )

    assert len(messages) == 1
    event = messages[0]
    assert event["type"] == "account.binding.accounts"
    assert len(event["accounts"]) == 2
    assert all(set(item) == {"accountRef", "accountMask", "accountType"} for item in event["accounts"])
    assert all(item["accountRef"] not in full_ids for item in event["accounts"])
    serialized = json.dumps(event, ensure_ascii=False)
    assert app_secret not in serialized
    assert all(account_id not in serialized for account_id in full_ids)


@pytest.mark.asyncio
async def test_binding_session_ref_is_one_time_and_cross_ref_is_rejected() -> None:
    completed: list[str] = []

    class BindingService:
        async def complete_account_binding(self, account_id: str) -> None:
            completed.append(account_id)

        def set_credential_state(self, _state: str) -> None:
            return None

        def connection_event(self) -> dict[str, Any]:
            return {"type": "connection"}

    application = app_module.FastOrderApplication.__new__(app_module.FastOrderApplication)
    application.service = BindingService()
    application.gateway = SimpleNamespace(account_id="")
    application._connection_errors = {}
    application._binding_sessions = {
        "session-one": {
            "expiresAt": time.monotonic() + 60,
            "appKey": "key",
            "appSecret": "secret",
            "remember": False,
            "autoConnect": False,
            "accounts": {"opaque-ref": "ACCOUNT-FULL-0001"},
        }
    }
    application._session_credentials = None
    application._resolved_credentials = None
    application.settings = SimpleNamespace(sandbox=True)
    application.credential_store = SimpleNamespace(
        load_profile=lambda: None,
        available=False,
    )

    async def broadcast(_event: dict[str, Any]) -> None:
        return None

    application.broadcast = broadcast
    await application._complete_binding("session-one", "opaque-ref")
    assert completed == ["ACCOUNT-FULL-0001"]
    assert "session-one" not in application._binding_sessions

    with pytest.raises(ValueError, match="会话已失效"):
        await application._complete_binding("session-one", "opaque-ref")

    application._binding_sessions["session-two"] = {
        "expiresAt": time.monotonic() + 60,
        "appKey": "key",
        "appSecret": "secret",
        "remember": False,
        "autoConnect": False,
        "accounts": {"different-ref": "ACCOUNT-FULL-0002"},
    }
    with pytest.raises(ValueError, match="账户选择无效"):
        await application._complete_binding("session-two", "opaque-ref")
    assert completed == ["ACCOUNT-FULL-0001"]


def test_connection_error_redacts_secrets_and_full_account_id() -> None:
    secret = "top-secret-value"
    account_id = "ACCOUNT-FULL-9988"
    safe = classify_connection_error(
        RuntimeError(
            f"unauthorized app_secret={secret}; account={account_id}; access_token=token-value"
        ),
        secrets=(secret, account_id),
    )

    assert safe.code == "AUTH_INVALID"
    assert safe.summary == "App Key 或 App Secret 无效"
    assert secret not in safe.detail
    assert account_id not in safe.detail
    assert "token-value" not in safe.detail
    assert "[REDACTED]" in safe.detail


@pytest.mark.asyncio
async def test_disconnect_disables_trading_before_gateway_shutdown(tmp_path: Path) -> None:
    observations: list[tuple[bool, bool]] = []

    class DisconnectGateway:
        connected = True
        stream_connected = True
        subscription_ready = True
        events_connected = True
        current_symbol = "AAPL"

        async def close(self) -> None:
            observations.append((service.account_transitioning, service.trading_enabled()))
            self.connected = False
            self.stream_connected = False

    async def publish(_event: dict[str, Any]) -> None:
        return None

    gateway = DisconnectGateway()
    service = TradingService(make_service_settings(tmp_path), gateway, publish)
    try:
        service.connection_status = "connected"
        service.last_market_at = time.time()
        assert service.trading_enabled() is True
        await service.disconnect()
        assert observations == [(True, False)]
        assert service.connection_status == "offline"
        assert service.trading_enabled() is False
    finally:
        service.ledger.close()


@pytest.mark.asyncio
async def test_forget_orders_disconnect_store_and_token_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class ForgetService:
        account_transitioning = False
        connection_status = "connected"
        connection_message = "connected"
        credential_state = "CREDENTIALS_SAVED"

        def connection_event(self) -> dict[str, Any]:
            return {"type": "connection", "accountTransitioning": self.account_transitioning}

        async def disconnect(self, _message: str) -> None:
            calls.append("disconnect")

        def set_credential_state(self, state: str) -> None:
            calls.append(f"state:{state}")
            self.credential_state = state

    class ForgetGateway:
        def clear_credentials(self) -> None:
            calls.append("clear-memory")

    class ForgetStore:
        available = True

        def load_profile(self) -> None:
            return None

        def delete_credentials(self) -> None:
            calls.append("delete-credential-manager")

    application = app_module.FastOrderApplication.__new__(app_module.FastOrderApplication)
    application.service = ForgetService()
    application.gateway = ForgetGateway()
    application.credential_store = ForgetStore()
    application.settings = SimpleNamespace(token_dir=tmp_path / "webull_tokens", sandbox=True)
    application._binding_sessions = {"pending": {}}
    application._session_credentials = ("key", "secret", "account")
    application._resolved_credentials = object()

    async def broadcast(_event: dict[str, Any]) -> None:
        return None

    def fake_rmtree(path: Path, *, ignore_errors: bool) -> None:
        assert path == application.settings.token_dir
        assert ignore_errors is True
        calls.append("delete-token")

    application.broadcast = broadcast
    monkeypatch.setattr(app_module.shutil, "rmtree", fake_rmtree)

    await application._forget_credentials()

    assert calls == [
        "disconnect",
        "clear-memory",
        "delete-credential-manager",
        "delete-token",
        "state:UNBOUND",
    ]
    assert application._binding_sessions == {}
    assert application._session_credentials is None
    assert application._resolved_credentials is None
    assert application.service.connection_status == "offline"
    assert application.service.credential_state == "UNBOUND"

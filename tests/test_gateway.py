from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from fast_order.errors import BrokerRejectedError, OrderUncertainError
from fast_order.webull_gateway import WebullGateway, _NoSdkFileLoggingDataStreamingMixin
from fast_order.config import Settings


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "project_root": tmp_path,
        "app_key": "key",
        "app_secret": "secret",
        "account_id": "acct-123456",
        "account_hint": "",
        "sandbox": True,
        "default_symbol": "AAPL",
        "max_order_qty": 100,
        "max_order_value": 10_000.0,
        "quote_stale_seconds": 8.0,
        "port": 8765,
        "sdk_connect_timeout": 0.2,
        "sdk_read_timeout": 0.2,
        "sdk_total_timeout": 0.2,
    }
    values.update(overrides)
    return Settings(**values)


def test_quote_client_start_disables_sdk_cwd_file_log(monkeypatch, tmp_path: Path) -> None:
    import paho.mqtt.client as mqtt_client
    import webull.data.internal.quotes_client as quotes_module

    install_dir = tmp_path / "Program Files" / "FastOrder"
    install_dir.mkdir(parents=True)
    monkeypatch.chdir(install_dir)

    gateway = WebullGateway(make_settings(tmp_path), lambda _event: None)
    gateway._build_streaming_client()
    client = gateway.streaming_client
    assert isinstance(client, _NoSdkFileLoggingDataStreamingMixin)

    monkeypatch.setattr(client, "_init_client", lambda: None)
    monkeypatch.setattr(client, "_quotes_connect", lambda *_args: 0)
    monkeypatch.setattr(mqtt_client.Client, "loop_forever", lambda *_args, **_kwargs: 0)

    def forbid_file_handler(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("SDK attempted to create a working-directory log")

    monkeypatch.setattr(quotes_module, "TimedRotatingFileHandler", forbid_file_handler)
    thread_failures: list[BaseException] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: thread_failures.append(args.exc_value))

    client.connect_and_loop_start(0.01, False, None)
    client.loop_wait()

    assert thread_failures == []
    assert not (install_dir / "webull_data_streaming_sdk.log").exists()
    assert list(install_dir.iterdir()) == []

    for sock_name in ("_sockpairR", "_sockpairW"):
        sock = getattr(client, sock_name, None)
        if sock is not None:
            sock.close()


@pytest.mark.asyncio
async def test_sdk_call_has_hard_deadline(tmp_path: Path) -> None:
    gateway = WebullGateway(make_settings(tmp_path), lambda _event: None)

    def hangs() -> None:
        import time

        time.sleep(1)

    with pytest.raises(TimeoutError):
        await gateway._sdk_call(hangs, timeout_name="sdk_total_timeout", default_timeout=0.01)


def test_market_message_requires_explicit_current_symbol(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []
    gateway = WebullGateway(make_settings(tmp_path), events.append)
    gateway._loop = asyncio.new_event_loop()
    gateway._handle_market_message("quote", {"basic": {"price": "1"}})
    gateway._handle_market_message("quote", {"basic": {"symbol": "MSFT", "price": "1"}})
    gateway._loop.close()
    assert events == []


@pytest.mark.asyncio
async def test_subscribe_keeps_connection_attempt_event(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []
    gateway = WebullGateway(make_settings(tmp_path), events.append)
    gateway._loop = asyncio.get_running_loop()
    gateway._stream_generation = 1
    original_ready = asyncio.Event()
    gateway._subscribe_ready = original_ready

    class FakeStreamingClient:
        def subscribe(self, *_args: object) -> None:
            gateway._handle_market_message(
                "quote",
                {"basic": {"symbol": "AAPL"}, "bids": [{"price": "100"}]},
                generation=1,
            )

    client = FakeStreamingClient()
    gateway.streaming_client = client
    await gateway._subscribe_stream_client(client, generation=1)

    assert gateway._subscribe_ready is original_ready
    assert original_ready.is_set()
    assert gateway.subscription_ready is True
    assert events and events[0]["type"] == "quote"


@pytest.mark.asyncio
async def test_get_open_orders_uses_sdk_order_open(tmp_path: Path) -> None:
    gateway = WebullGateway(make_settings(tmp_path), lambda _event: None)
    gateway.account_id = "acct-123456"
    gateway.trade_client = SimpleNamespace(
        order_v3=SimpleNamespace(
            get_order_open=lambda account_id, page_size=None: {"orders": [{"client_order_id": "co-1"}]}
        )
    )
    assert await gateway.get_open_orders() == [{"client_order_id": "co-1"}]


@pytest.mark.asyncio
async def test_usd_buying_power_selects_only_usd_currency_asset(tmp_path: Path) -> None:
    gateway = WebullGateway(make_settings(tmp_path), lambda _event: None)
    gateway.account_id = "acct-123456"
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "account_currency_assets": [
                {"currency": "HKD", "buying_power": "999999"},
                {"currency": "USD", "buying_power": "1234.5600"},
            ]
        },
    )
    gateway.trade_client = SimpleNamespace(
        account_v2=SimpleNamespace(get_account_balance=lambda _account_id: response)
    )

    assert await gateway.get_usd_buying_power() == "1234.5600"


@pytest.mark.asyncio
async def test_usd_buying_power_does_not_fallback_to_other_currency(tmp_path: Path) -> None:
    gateway = WebullGateway(make_settings(tmp_path), lambda _event: None)
    gateway.account_id = "acct-123456"
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {"account_currency_assets": [{"currency": "HKD", "buying_power": "999999"}]},
    )
    gateway.trade_client = SimpleNamespace(
        account_v2=SimpleNamespace(get_account_balance=lambda _account_id: response)
    )

    assert await gateway.get_usd_buying_power() is None


@pytest.mark.asyncio
async def test_positions_project_only_nonzero_usd_equities_without_sensitive_fields(tmp_path: Path) -> None:
    gateway = WebullGateway(make_settings(tmp_path), lambda _event: None)
    gateway.account_id = "acct-should-never-reach-browser"
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "data": {
                "account_id": "acct-should-never-reach-browser",
                "holdings": [
                    {
                        "symbol": "AAPL", "short_name": "Apple Inc", "currency": "USD",
                        "instrument_type": "STOCK", "qty": "3.0000", "last_price": "201.50",
                        "unit_cost": "180", "unrealized_profit_loss": "64.5000", "token": "secret",
                    },
                    {
                        "symbol": "QQQ", "short_name": "Invesco QQQ", "currency": "USD",
                        "instrument_type": "ETF", "qty": "0", "last_price": "500",
                        "unit_cost": "450", "unrealized_profit_loss": "0",
                    },
                    {
                        "symbol": "0700", "short_name": "Tencent", "currency": "HKD",
                        "instrument_type": "STOCK", "qty": "10", "last_price": "600",
                        "unit_cost": "500", "unrealized_profit_loss": "1000",
                    },
                ],
            }
        },
    )
    gateway.trade_client = SimpleNamespace(
        account_v2=SimpleNamespace(get_account_position=lambda _account_id: response)
    )

    positions = await gateway.get_positions()

    assert positions == [{
        "symbol": "AAPL", "name": "Apple Inc", "lastPrice": "201.5",
        "quantity": "3", "unitCost": "180", "unrealizedPnl": "64.5",
    }]
    assert "account" not in repr(positions).lower()
    assert "secret" not in repr(positions).lower()


@pytest.mark.asyncio
async def test_positions_accept_sdk_name_aliases(tmp_path: Path) -> None:
    gateway = WebullGateway(make_settings(tmp_path), lambda _event: None)
    gateway.account_id = "acct-hidden"
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "holdings": [{
                "symbol": "MSFT", "symbolName": "Microsoft Corp", "currency": "USD",
                "instrument_type": "STOCK", "qty": "2", "last_price": "420",
                "unit_cost": "400", "unrealized_profit_loss": "40",
            }]
        },
    )
    gateway.trade_client = SimpleNamespace(
        account_v2=SimpleNamespace(get_account_position=lambda _account_id: response)
    )

    assert (await gateway.get_positions())[0]["name"] == "Microsoft Corp"


@pytest.mark.asyncio
async def test_place_order_classifies_broker_rejection(tmp_path: Path) -> None:
    gateway = WebullGateway(make_settings(tmp_path), lambda _event: None)
    gateway.account_id = "acct-123456"
    gateway.connected = True
    gateway.stream_connected = True
    gateway.subscription_ready = True
    gateway.events_healthy = True
    gateway.trade_client = SimpleNamespace(
        order_v3=SimpleNamespace(
            place_order=lambda *_args: SimpleNamespace(status_code=400, json=lambda: {"error": "bad order"})
        )
    )
    with pytest.raises(BrokerRejectedError):
        await gateway.place_order(
            {"clientOrderId": "co-1", "symbol": "AAPL", "orderType": "MARKET", "quantity": 1, "side": "BUY"}
        )


@pytest.mark.asyncio
async def test_place_order_never_retries_uncertain_transport(tmp_path: Path) -> None:
    calls = 0

    def fails(*_args: object) -> None:
        nonlocal calls
        calls += 1
        raise OSError("connection reset")

    gateway = WebullGateway(make_settings(tmp_path), lambda _event: None)
    gateway.account_id = "acct-123456"
    gateway.connected = True
    gateway.stream_connected = True
    gateway.subscription_ready = True
    gateway.events_healthy = True
    gateway.trade_client = SimpleNamespace(order_v3=SimpleNamespace(place_order=fails))
    with pytest.raises(OrderUncertainError):
        await gateway.place_order(
            {"clientOrderId": "co-1", "symbol": "AAPL", "orderType": "MARKET", "quantity": 1, "side": "BUY"}
        )
    assert calls == 1

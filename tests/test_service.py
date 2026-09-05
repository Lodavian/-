from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from fast_order.config import Settings
from fast_order.errors import BrokerRejectedError
from fast_order.service import TradingService


class FakeGateway:
    def __init__(self):
        self.connected = False
        self.stream_connected = False
        self.current_symbol = "AAPL"
        self.available_position = 20.0
        self.place_calls = 0
        self.cancel_calls = 0
        self.switches: list[str] = []
        self.place_error: BaseException | None = None
        self.detail: Any | None = None
        self.place_delay = 0.0
        self.connect_error: BaseException | None = None
        self.instrument_results: list[dict[str, str]] = []
        self.recovery_calls = 0
        self.recovery_error: BaseException | None = None
        self.recovery_events: list[dict[str, Any]] = []
        self.market_activity: list[bool] = []
        self.usd_buying_power: str | None = "1234.56"
        self.positions: list[dict[str, str]] = []
        self.position_calls = 0
        self.positions_error: BaseException | None = None
        self.positions_delay = 0.0

    async def connect(self) -> dict[str, Any]:
        if self.connect_error:
            raise self.connect_error
        self.connected = True
        self.stream_connected = True
        return {"accountId": "abc***xyz", "symbol": self.current_symbol}

    async def switch_symbol(self, symbol: str) -> None:
        self.switches.append(symbol)
        self.current_symbol = symbol

    async def search_instruments(self, _query: str) -> list[dict[str, str]]:
        return self.instrument_results

    async def recover_market(self, _symbol: str) -> list[dict[str, Any]]:
        self.recovery_calls += 1
        if self.recovery_error:
            raise self.recovery_error
        return self.recovery_events

    async def set_market_active(self, active: bool) -> None:
        self.market_activity.append(active)

    async def get_available_position(self, _symbol: str) -> float:
        return self.available_position

    async def get_positions(self) -> list[dict[str, str]]:
        self.position_calls += 1
        if self.positions_delay:
            await asyncio.sleep(self.positions_delay)
        if self.positions_error:
            raise self.positions_error
        return [dict(item) for item in self.positions]

    async def get_usd_buying_power(self) -> str | None:
        return self.usd_buying_power

    async def place_order(self, order: dict[str, Any]) -> Any:
        self.place_calls += 1
        if self.place_delay:
            await asyncio.sleep(self.place_delay)
        if self.place_error:
            raise self.place_error
        return {"client_order_id": order["clientOrderId"], "status": "OPEN"}

    async def get_order_detail(self, _client_order_id: str) -> Any | None:
        return self.detail

    async def cancel_order(self, _client_order_id: str) -> Any:
        self.cancel_calls += 1
        return {"ok": True}

    async def close(self) -> None:
        self.connected = False
        self.stream_connected = False


def settings(project_root: Path) -> Settings:
    return Settings(
        project_root=project_root,
        app_key="key",
        app_secret="secret",
        account_id="account",
        account_hint="",
        sandbox=True,
        default_symbol="AAPL",
        max_order_qty=100,
        max_order_value=10_000,
        quote_stale_seconds=5,
        port=8765,
        max_open_orders=10,
    )


@pytest.fixture
def setup_service(tmp_path: Path, request: pytest.FixtureRequest) -> tuple[TradingService, FakeGateway, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []

    async def publish(event: dict[str, Any]) -> None:
        events.append(event)

    gateway = FakeGateway()
    service = TradingService(settings(tmp_path), gateway, publish)
    request.addfinalizer(service.ledger.close)
    return service, gateway, events


async def make_ready(service: TradingService, gateway: FakeGateway, price: float = 100.0) -> None:
    gateway.connected = True
    gateway.stream_connected = True
    service.connection_status = "connected"
    service.receive_gateway_event(
        {
            "type": "snapshot",
            "symbol": "AAPL",
            "receivedAt": time.time(),
            "data": {"price": str(price), "pre_close": "99"},
        }
    )
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_connect_failure_is_reported(setup_service) -> None:
    service, gateway, events = setup_service
    gateway.connect_error = RuntimeError("network unavailable")
    with pytest.raises(RuntimeError, match="network unavailable"):
        await service.connect()
    assert service.connection_status == "error"
    assert events[-1]["status"] == "error"


@pytest.mark.asyncio
async def test_connect_reads_and_publishes_usd_buying_power(setup_service) -> None:
    service, _gateway, events = setup_service

    await service.connect()

    assert service.usd_buying_power == "1234.56"
    assert any(event.get("type") == "risk.state" and event.get("usdBuyingPower") == "1234.56" for event in events)


@pytest.mark.asyncio
async def test_connect_immediately_publishes_normalized_positions(setup_service) -> None:
    service, gateway, events = setup_service
    gateway.positions = [{
        "symbol": "AAPL", "name": "Apple Inc", "lastPrice": "201.5",
        "quantity": "3", "unitCost": "180", "unrealizedPnl": "64.5",
    }]

    await service.connect()

    assert gateway.position_calls == 1
    assert service.positions == gateway.positions
    assert any(event.get("type") == "positions" and event.get("status") == "ready" for event in events)


@pytest.mark.asyncio
async def test_position_refresh_coalesces_and_failure_preserves_last_snapshot(setup_service) -> None:
    service, gateway, _events = setup_service
    await make_ready(service, gateway)
    gateway.positions = [{
        "symbol": "MSFT", "name": "Microsoft", "lastPrice": "420",
        "quantity": "2", "unitCost": "400", "unrealizedPnl": "40",
    }]
    gateway.positions_delay = 0.02

    results = await asyncio.gather(*(service.refresh_positions("test") for _ in range(3)))

    assert gateway.position_calls == 1
    assert all(result["positions"] == gateway.positions for result in results)
    gateway.positions_error = RuntimeError("sensitive raw response")
    failed = await service.refresh_positions("failure")
    assert failed["status"] == "error"
    assert failed["positions"] == gateway.positions
    assert "sensitive raw response" not in failed["message"]


@pytest.mark.asyncio
async def test_positions_pause_in_background_and_refresh_on_foreground(setup_service) -> None:
    service, gateway, _events = setup_service
    await make_ready(service, gateway)
    await service.set_market_active(False)
    assert service._positions_timer_task is None

    await service.set_market_active(True)

    assert gateway.position_calls == 1
    assert service._positions_timer_task is not None
    await service.set_market_active(False)


@pytest.mark.asyncio
async def test_fill_event_refreshes_positions_without_affecting_switch(setup_service) -> None:
    service, gateway, _events = setup_service
    await make_ready(service, gateway)
    placed = await service.submit_order(order_payload(requestId="position-fill"))
    client_id = placed["order"]["clientOrderId"]
    service.receive_gateway_event({
        "type": "order.update",
        "order": {"client_order_id": client_id, "order_status": "PARTIAL_FILLED"},
    })
    await asyncio.sleep(0.02)

    assert gateway.position_calls == 1


@pytest.mark.asyncio
async def test_switch_symbol_clears_stale_quote_and_calls_gateway(setup_service) -> None:
    service, gateway, _events = setup_service
    await make_ready(service, gateway)
    await service.switch_symbol("msft")
    assert gateway.switches == ["MSFT"]
    assert service.current_symbol == "MSFT"
    assert service.last_price is None
    assert service.last_market_at == 0


@pytest.mark.asyncio
async def test_stale_or_disconnected_market_blocks_order(setup_service) -> None:
    service, gateway, _events = setup_service
    gateway.connected = True
    gateway.stream_connected = True
    service.last_market_at = time.time() - 20
    service.last_price = service._positive_decimal("100", "价格")
    with pytest.raises(RuntimeError, match="过期"):
        await service.submit_order(order_payload())


@pytest.mark.asyncio
async def test_quiet_stream_is_refreshed_by_rest_recovery(setup_service) -> None:
    service, gateway, _events = setup_service
    gateway.connected = True
    gateway.stream_connected = True
    service.connection_status = "connected"
    service.last_market_at = time.time() - 20
    gateway.recovery_events = [
        {
            "type": "snapshot",
            "symbol": "AAPL",
            "receivedAt": time.time(),
            "source": "rest-recovery",
            "data": {"symbol": "AAPL", "price": "101.25", "pre_close": "100"},
        }
    ]
    assert await service.maintain_market_freshness()
    assert gateway.recovery_calls == 1
    assert service.trading_enabled()
    assert service.last_price == service._positive_decimal("101.25", "价格")


@pytest.mark.asyncio
async def test_recovery_is_not_called_for_fresh_or_disconnected_stream(setup_service) -> None:
    service, gateway, _events = setup_service
    await make_ready(service, gateway)
    assert not await service.maintain_market_freshness()
    gateway.stream_connected = False
    service.last_market_at = time.time() - 20
    assert not await service.maintain_market_freshness()
    assert gateway.recovery_calls == 0


@pytest.mark.asyncio
async def test_failed_recovery_keeps_stale_market_blocked(setup_service) -> None:
    service, gateway, _events = setup_service
    gateway.connected = True
    gateway.stream_connected = True
    service.last_market_at = time.time() - 20
    gateway.recovery_error = RuntimeError("temporary data error")
    assert not await service.maintain_market_freshness()
    assert not service.trading_enabled()
    assert service.block_reason() == "实时行情已过期"


@pytest.mark.asyncio
async def test_background_pauses_market_and_foreground_resumes_with_recovery(setup_service) -> None:
    service, gateway, events = setup_service
    gateway.connected = True
    gateway.stream_connected = True
    service.last_market_at = time.time() - 20
    gateway.recovery_events = [
        {
            "type": "snapshot",
            "symbol": "AAPL",
            "receivedAt": time.time(),
            "data": {"symbol": "AAPL", "price": "102"},
        }
    ]
    await service.set_market_active(False)
    assert gateway.market_activity == [False]
    assert service.block_reason() == "页面在后台，行情刷新已暂停"
    assert not service.trading_enabled()
    await service.set_market_active(True)
    assert gateway.market_activity == [False, True]
    assert gateway.recovery_calls == 1
    assert service.trading_enabled()
    assert any(event.get("marketActive") is True for event in events if event.get("type") == "connection")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"quantity": "0"}, "正整数"),
        ({"quantity": "1.5"}, "正整数"),
        ({"quantity": "101"}, "数量超过"),
        ({"orderType": "LIMIT", "limitPrice": "0"}, "限价必须"),
        ({"orderType": "STOP"}, "订单类型"),
        ({"symbol": "MSFT"}, "当前订阅"),
    ],
)
async def test_order_validation(setup_service, changes: dict[str, Any], message: str) -> None:
    service, gateway, _events = setup_service
    await make_ready(service, gateway)
    payload = order_payload()
    payload.update(changes)
    with pytest.raises((ValueError, RuntimeError), match=message):
        await service.submit_order(payload)
    assert gateway.place_calls == 0


@pytest.mark.asyncio
async def test_order_value_limit_is_server_side(setup_service) -> None:
    service, gateway, _events = setup_service
    await make_ready(service, gateway, price=200)
    payload = order_payload(quantity="100")
    with pytest.raises(ValueError, match="订单金额"):
        await service.submit_order(payload)


@pytest.mark.asyncio
async def test_sell_cannot_exceed_available_position(setup_service) -> None:
    service, gateway, _events = setup_service
    await make_ready(service, gateway)
    gateway.available_position = 2
    with pytest.raises(ValueError, match="可卖持仓不足"):
        await service.submit_order(order_payload(side="SELL", quantity="3"))
    assert gateway.place_calls == 0


@pytest.mark.asyncio
async def test_market_and_limit_orders_submit_expected_fields(setup_service) -> None:
    service, gateway, _events = setup_service
    await make_ready(service, gateway)
    market = await service.submit_order(order_payload(requestId="market"))
    limit = await service.submit_order(order_payload(requestId="limit", orderType="LIMIT", limitPrice="99.50"))
    assert market["order"]["status"] == "ACKNOWLEDGED"
    assert limit["order"]["limitPrice"] == "99.50"
    assert gateway.place_calls == 2


@pytest.mark.asyncio
async def test_concurrent_duplicate_request_places_only_once(setup_service) -> None:
    service, gateway, _events = setup_service
    await make_ready(service, gateway)
    gateway.place_delay = 0.03
    payload = order_payload(requestId="same-request")
    first, second = await asyncio.gather(service.submit_order(payload), service.submit_order(payload))
    assert first == second
    assert gateway.place_calls == 1


@pytest.mark.asyncio
async def test_timeout_reconciles_without_resubmission(setup_service) -> None:
    service, gateway, _events = setup_service
    await make_ready(service, gateway)
    gateway.place_error = TimeoutError("timeout")
    gateway.detail = {"status": "OPEN", "order_id": "broker-order"}
    result = await service.submit_order(order_payload(requestId="timeout"))
    assert result["order"]["status"] == "OPEN"
    assert gateway.place_calls == 1


@pytest.mark.asyncio
async def test_unresolved_timeout_is_marked_uncertain(setup_service) -> None:
    service, gateway, _events = setup_service
    await make_ready(service, gateway)
    gateway.place_error = TimeoutError("timeout")
    result = await service.submit_order(order_payload(requestId="uncertain"))
    assert result["order"]["status"] == "UNKNOWN"
    assert gateway.place_calls == 1


@pytest.mark.asyncio
async def test_rejected_order_is_cached_for_idempotency(setup_service) -> None:
    service, gateway, _events = setup_service
    await make_ready(service, gateway)
    gateway.place_error = BrokerRejectedError("broker rejected")
    payload = order_payload(requestId="rejected")
    first = await service.submit_order(payload)
    second = await service.submit_order(payload)
    assert first["order"]["status"] == "REJECTED"
    assert second == first
    assert gateway.place_calls == 1


@pytest.mark.asyncio
async def test_open_order_can_be_cancelled(setup_service) -> None:
    service, gateway, _events = setup_service
    await make_ready(service, gateway)
    result = await service.submit_order(order_payload(requestId="cancel"))
    client_id = result["order"]["clientOrderId"]
    cancelled = await service.cancel_order(client_id)
    assert cancelled["order"]["status"] == "CANCEL_REQUESTED"
    assert gateway.cancel_calls == 1


def order_payload(**changes: Any) -> dict[str, Any]:
    payload = {
        "requestId": "request-1",
        "symbol": "AAPL",
        "side": "BUY",
        "orderType": "MARKET",
        "quantity": "1",
    }
    payload.update(changes)
    return payload

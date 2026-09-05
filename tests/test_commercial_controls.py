from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from fast_order.service import TradingService
from tests.test_service import FakeGateway, make_ready, order_payload, settings


def build_service(root: Path, **overrides: Any) -> tuple[TradingService, FakeGateway]:
    async def publish(_event: dict[str, Any]) -> None:
        return None

    configured = replace(settings(root), **overrides)
    gateway = FakeGateway()
    return TradingService(configured, gateway, publish), gateway


@pytest.mark.asyncio
async def test_same_request_with_different_payload_is_rejected(tmp_path: Path) -> None:
    service, gateway = build_service(tmp_path)
    try:
        await make_ready(service, gateway)
        await service.submit_order(order_payload(requestId="stable-request"))
        changed = order_payload(requestId="stable-request", quantity="2")
        with pytest.raises(ValueError, match="requestId"):
            await service.submit_order(changed)
        assert gateway.place_calls == 1
    finally:
        service.ledger.close()


@pytest.mark.asyncio
async def test_idempotency_survives_process_restart(tmp_path: Path) -> None:
    first, first_gateway = build_service(tmp_path)
    await make_ready(first, first_gateway)
    original = await first.submit_order(order_payload(requestId="restart-safe"))
    first.ledger.close()

    second, second_gateway = build_service(tmp_path)
    try:
        repeated = await second.submit_order(order_payload(requestId="restart-safe"))
        assert repeated["order"]["clientOrderId"] == original["order"]["clientOrderId"]
        assert second_gateway.place_calls == 0
    finally:
        second.ledger.close()


@pytest.mark.asyncio
async def test_different_orders_are_serialized_and_open_order_cap_applies(tmp_path: Path) -> None:
    service, gateway = build_service(tmp_path, max_open_orders=1)
    try:
        await make_ready(service, gateway)
        gateway.place_delay = 0.03
        outcomes = await asyncio.gather(
            service.submit_order(order_payload(requestId="first")),
            service.submit_order(order_payload(requestId="second")),
            return_exceptions=True,
        )
        assert gateway.place_calls == 1
        assert sum(isinstance(item, RuntimeError) for item in outcomes) == 1
    finally:
        service.ledger.close()


@pytest.mark.asyncio
async def test_unknown_submission_is_persisted_and_blocks_further_orders(tmp_path: Path) -> None:
    service, gateway = build_service(tmp_path)
    try:
        await make_ready(service, gateway)
        gateway.place_error = TimeoutError("secret broker response")
        result = await service.submit_order(order_payload(requestId="unknown"))
        assert result["order"]["status"] == "UNKNOWN"
        assert result["order"]["message"].endswith("TimeoutError")
        assert not service.trading_enabled()
        assert "对账" in service.block_reason()
    finally:
        service.ledger.close()


@pytest.mark.asyncio
async def test_naked_market_order_uses_stricter_cap(tmp_path: Path) -> None:
    service, gateway = build_service(tmp_path, max_market_qty=1)
    try:
        await make_ready(service, gateway)
        with pytest.raises(ValueError, match="市价单"):
            await service.submit_order(order_payload(requestId="market-cap", quantity="2"))
        assert gateway.place_calls == 0
    finally:
        service.ledger.close()


@pytest.mark.asyncio
async def test_live_switch_requires_account_and_background_keeps_choice(tmp_path: Path) -> None:
    service, gateway = build_service(
        tmp_path,
        sandbox=False,
        live_allowed=True,
    )
    try:
        with pytest.raises(RuntimeError, match="连接交易账户"):
            service.arm_live()
        gateway.connected = True
        gateway.stream_connected = True
        service.connection_status = "connected"
        service.account_mask = "abc***xyz"
        state = service.arm_live()
        assert state["liveArmed"] is True
        await service.set_market_active(False)
        assert service.risk_event()["liveArmed"] is True
        assert service.trading_enabled() is False
        service.disarm_live("交易开关已关闭")
        assert service.risk_event()["liveArmed"] is False
    finally:
        service.ledger.close()


def test_live_switch_has_no_time_expiry(tmp_path: Path) -> None:
    service, _gateway = build_service(
        tmp_path,
        sandbox=False,
        live_allowed=True,
    )
    try:
        _gateway.connected = True
        service.connection_status = "connected"
        service.account_mask = "abc***xyz"
        service.arm_live()
        assert service._live_is_armed(now=10**12) is True
    finally:
        service.ledger.close()


@pytest.mark.asyncio
async def test_cancel_failed_event_restores_cancellable_status(tmp_path: Path) -> None:
    service, gateway = build_service(tmp_path)
    try:
        await make_ready(service, gateway)
        placed = await service.submit_order(order_payload(requestId="cancel-failed"))
        client_id = placed["order"]["clientOrderId"]
        await service.cancel_order(client_id)
        assert service.orders[0]["status"] == "CANCEL_REQUESTED"
        service.receive_gateway_event(
            {
                "type": "order.update",
                "order": {
                    "client_order_id": client_id,
                    "order_status": "SUBMITTED",
                    "scene_type": "CANCEL_FAILED",
                },
            }
        )
        await asyncio.sleep(0)
        assert service.orders[0]["status"] == "SUBMITTED"
    finally:
        service.ledger.close()

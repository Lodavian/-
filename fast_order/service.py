from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import datetime, time as clock_time
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .config import Settings
from .errors import BrokerRejectedError, OrderUncertainError
from .ledger import ACTIVE_STATUSES, OrderLedger
from .utils import find_number, normalize_instrument_query, normalize_symbol, redact, redact_text


EventPublisher = Callable[[dict[str, Any]], Awaitable[None]]

STATUS_ALIASES = {
    "PARTIAL": "PARTIAL_FILLED",
    "PARTIALLY_FILLED": "PARTIAL_FILLED",
    "CANCELED": "CANCELLED",
    "PENDING_CANCEL": "CANCEL_REQUESTED",
    "FAILED": "REJECTED",
}
STATUS_RANK = {
    "PREPARED": 0,
    "SENDING": 1,
    "UNKNOWN": 2,
    "ACKNOWLEDGED": 3,
    "SUBMITTED": 3,
    "OPEN": 4,
    "CANCEL_REQUESTED": 5,
    "PARTIAL_FILLED": 6,
    "REJECTED": 10,
    "CANCELLED": 10,
    "FILLED": 10,
}


class Gateway(Protocol):
    connected: bool
    stream_connected: bool
    current_symbol: str

    async def connect(self, market_active: bool = True) -> dict[str, Any]: ...
    async def discover_accounts(self, app_key: str, app_secret: str) -> list[dict[str, Any]]: ...
    async def connect_selected(self, account_id: str, market_active: bool = True) -> dict[str, Any]: ...
    async def switch_symbol(self, symbol: str) -> None: ...
    async def search_instruments(self, query: str) -> list[dict[str, str]]: ...
    async def recover_market(self, symbol: str) -> list[dict[str, Any]]: ...
    async def set_market_active(self, active: bool) -> None: ...
    async def get_available_position(self, symbol: str) -> float: ...
    async def get_positions(self) -> list[dict[str, str]]: ...
    async def get_usd_buying_power(self) -> str | None: ...
    async def place_order(self, order: dict[str, Any]) -> Any: ...
    async def get_order_detail(self, client_order_id: str) -> Any | None: ...
    async def cancel_order(self, client_order_id: str) -> Any: ...
    async def close(self) -> None: ...


class TradingService:
    def __init__(
        self,
        settings: Settings,
        gateway: Gateway,
        publisher: EventPublisher,
        *,
        market_active: bool = True,
    ):
        self.settings = settings
        self.gateway = gateway
        self.publisher = publisher
        self.current_symbol = normalize_symbol(settings.default_symbol)
        self.connection_status = "offline"
        self.connection_message = "尚未连接券商API"
        self.credential_state = "ENV_FALLBACK" if getattr(gateway, "has_credentials", False) else "UNBOUND"
        self.switching_symbol = False
        self.account_transitioning = False
        self.market_active = market_active
        self.last_market_at = 0.0
        self.last_price: Decimal | None = None
        self.quote: dict[str, Any] = {}
        self.snapshot: dict[str, Any] = {}
        self.ticks: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        # Public position projection.  Keep broker/account identifiers out of
        # this state; the gateway returns only the six UI fields.
        self.positions: list[dict[str, str]] = []
        self.positions_updated_at: float | None = None
        self.positions_status = "not_connected"
        self.positions_message = "账户未连接"
        self.ledger = OrderLedger(settings.project_root / "runtime" / "orders.db")
        self.orders = self.ledger.recent()
        self._connect_lock = asyncio.Lock()
        self._symbol_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._idempotency_lock = asyncio.Lock()
        self._recovery_lock = asyncio.Lock()
        self._positions_refresh_lock = asyncio.Lock()
        self._positions_inflight: asyncio.Task[dict[str, Any]] | None = None
        self._positions_timer_task: asyncio.Task[None] | None = None
        self._positions_epoch = 0
        self._last_recovery_attempt = 0.0
        self._request_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._request_results: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.live_armed = settings.sandbox
        self.account_mask = ""
        self.usd_buying_power: str | None = None
        self.controller_id = ""
        self._unknown_order_ids: set[str] = {
            str(item["clientOrderId"]) for item in self.orders if item.get("status") in {"PREPARED", "SENDING", "UNKNOWN"}
        }
        self._logger = logging.getLogger("fast_order.market_recovery")

    def positions_event(self) -> dict[str, Any]:
        """Return the websocket-safe position state/event."""
        return {
            "type": "positions",
            "positions": [dict(item) for item in self.positions],
            "updatedAt": self.positions_updated_at,
            "refreshStatus": self.positions_status,
            "status": self.positions_status,
            "message": self.positions_message,
        }

    async def refresh_positions(self, reason: str = "manual") -> dict[str, Any]:
        """Refresh positions, coalescing callers sharing one broker request."""
        if not self.gateway.connected:
            self.positions = []
            self.positions_updated_at = None
            self.positions_status = "not_connected"
            self.positions_message = "账户未连接"
            event = self.positions_event()
            await self.publisher(event)
            return event

        async with self._positions_refresh_lock:
            task = self._positions_inflight
            if task is None or task.done():
                self.positions_status = "loading"
                self.positions_message = "正在读取持仓"
                await self.publisher(self.positions_event())
                epoch = self._positions_epoch
                task = asyncio.create_task(self._perform_positions_refresh(epoch, reason), name="fast-order-positions")
                self._positions_inflight = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self._positions_inflight is task:
                self._positions_inflight = None

    async def _perform_positions_refresh(self, epoch: int, reason: str) -> dict[str, Any]:
        del reason  # retained for call-site diagnostics and future metrics
        try:
            method = getattr(self.gateway, "get_positions", None)
            if not callable(method):
                raise RuntimeError("持仓查询接口不可用")
            positions = await method()
            # An account transition can finish while the SDK request is in
            # flight. Never allow the old account response to repopulate state.
            if epoch != self._positions_epoch or not self.gateway.connected:
                return self.positions_event()
            self.positions = [dict(item) for item in positions if isinstance(item, dict)]
            self.positions_updated_at = time.time()
            self.positions_status = "ready"
            self.positions_message = ""
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if epoch == self._positions_epoch and self.gateway.connected:
                # Preserve the last successful snapshot on failure.
                self.positions_status = "error"
                self.positions_message = "持仓更新失败"
                self._logger.warning("持仓读取失败: %s", type(exc).__name__)
        event = self.positions_event()
        await self.publisher(event)
        return event

    def _schedule_positions_refresh(self, reason: str) -> None:
        if not self.gateway.connected:
            return
        try:
            asyncio.create_task(self.refresh_positions(reason), name="fast-order-positions-trigger")
        except RuntimeError:
            # Gateway callbacks should run on the service loop; tolerate a
            # late callback during loop shutdown without leaking an exception.
            return

    def _start_positions_timer(self) -> None:
        if self._positions_timer_task is None or self._positions_timer_task.done():
            self._positions_timer_task = asyncio.create_task(self._positions_refresh_loop(), name="fast-order-positions-timer")

    def _stop_positions_timer(self) -> None:
        task = self._positions_timer_task
        self._positions_timer_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _positions_refresh_loop(self) -> None:
        try:
            while self.gateway.connected and self.market_active:
                await asyncio.sleep(15)
                if self.gateway.connected and self.market_active:
                    await self.refresh_positions("interval")
        except asyncio.CancelledError:
            pass

    def receive_gateway_event(self, event: dict[str, Any]) -> None:
        kind = str(event.get("type") or "")
        if kind in {"quote", "snapshot", "tick"}:
            if str(event.get("symbol") or "").upper() != self.current_symbol:
                return
            self.last_market_at = float(event.get("receivedAt") or time.time())
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            if kind == "quote":
                self.quote = data
            elif kind == "snapshot":
                self.snapshot = data
            else:
                self.ticks.insert(0, data)
                self.ticks = self.ticks[:20]
            price = self._extract_price(kind, data)
            if price is not None:
                self.last_price = price
        elif kind == "connection":
            self.connection_status = str(event.get("status") or self.connection_status)
            self.connection_message = str(event.get("message") or self.connection_message)
        elif kind == "order.update":
            update = event.get("order") if isinstance(event.get("order"), dict) else {}
            client_id = str(
                update.get("client_order_id")
                or update.get("clientOrderId")
                or update.get("client_orderid")
                or ""
            )
            current = next((item for item in self.orders if item.get("clientOrderId") == client_id), None)
            if current is not None:
                raw_status = self._normalize_status(update.get("status") or update.get("order_status") or update.get("state") or "OPEN")
                old_status = self._normalize_status(current.get("status") or "PREPARED")
                scene_type = str(update.get("scene_type") or update.get("sceneType") or "").upper()
                if scene_type == "CANCEL_FAILED" and old_status == "CANCEL_REQUESTED" and raw_status in {"SUBMITTED", "OPEN"}:
                    current["status"] = raw_status
                elif STATUS_RANK.get(raw_status, 4) >= STATUS_RANK.get(old_status, 0):
                    current["status"] = raw_status
                current["event"] = redact(update)
                persisted = self.ledger.update(client_id, current["status"], result=redact(update))
                if persisted is not None:
                    current.update(persisted)
                if current["status"] not in {"PREPARED", "SENDING", "UNKNOWN"}:
                    self._unknown_order_ids.discard(client_id)
                event = {"type": "order.update", "order": dict(current), "receivedAt": event.get("receivedAt")}
                if raw_status in {"PARTIAL_FILLED", "FILLED"}:
                    self._schedule_positions_refresh("order-fill")
        asyncio.create_task(self.publisher(self._public_event(event)))

    async def connect(self) -> dict[str, Any]:
        async with self._connect_lock:
            if not self.settings.sandbox:
                self.disarm_live("账户重新连接后需要重新解锁")
            self.connection_status = "connecting"
            self.connection_message = "正在连接券商API"
            await self.publisher(self.connection_event())
            try:
                try:
                    result = await self.gateway.connect(market_active=self.market_active)
                except TypeError as exc:
                    if "market_active" not in str(exc):
                        raise
                    result = await self.gateway.connect()
            except Exception as exc:
                self.connection_status = "error"
                self.connection_message = "账户连接失败，请检查 API 凭据或网络"
                await self.publisher(self.connection_event())
                raise
            return await self._finish_connection(result)

    async def begin_account_binding(self, app_key: str, app_secret: str) -> list[dict[str, Any]]:
        async with self._connect_lock:
            if self.gateway.connected:
                raise RuntimeError("已连接时不能替换 API 凭据，请先断开账户")
            self.connection_status = "connecting"
            self.connection_message = "正在验证券商API凭据"
            await self.publisher(self.connection_event())
            try:
                accounts = await self.gateway.discover_accounts(app_key, app_secret)
            except Exception:
                self.connection_status = "error"
                self.connection_message = "API 凭据验证失败"
                await self.publisher(self.connection_event())
                raise
            self.connection_status = "offline"
            self.connection_message = "凭据有效，请选择交易账户"
            await self.publisher(self.connection_event())
            return accounts

    async def complete_account_binding(self, account_id: str) -> dict[str, Any]:
        async with self._connect_lock:
            if self.gateway.connected:
                raise RuntimeError("券商API已连接，不能热切换账户")
            if not self.settings.sandbox:
                self.disarm_live("账户重新连接后需要重新解锁")
            self.connection_status = "connecting"
            self.connection_message = "正在连接所选账户"
            await self.publisher(self.connection_event())
            try:
                result = await self.gateway.connect_selected(account_id, market_active=self.market_active)
            except Exception:
                self.connection_status = "error"
                self.connection_message = "所选账户连接失败"
                await self.publisher(self.connection_event())
                raise
            return await self._finish_connection(result)

    async def _finish_connection(self, result: dict[str, Any]) -> dict[str, Any]:
        self.current_symbol = self.gateway.current_symbol
        self.account_mask = str(result.get("accountId") or "")
        events_ready = bool(result.get("eventsConnected", getattr(self.gateway, "events_connected", False)))
        self.connection_status = "connected" if events_ready else "degraded"
        self.connection_message = (
            "账户与实时行情已连接"
            if events_ready
            else "账户与行情已连接，订单事件通道仍在连接；新订单已禁用"
        )
        try:
            self.usd_buying_power = await self.gateway.get_usd_buying_power()
        except Exception as exc:
            self.usd_buying_power = None
            self._logger.warning("美元购买力读取失败: %s", type(exc).__name__)
        # Position reads are best-effort and never make a healthy account
        # connection fail. This also gives the UI data immediately after login.
        await self.refresh_positions("connected")
        if self.market_active:
            self._start_positions_timer()
        await self.publisher(self.risk_event())
        await self.reconcile_unresolved()
        event = self.connection_event()
        event.update(result)
        await self.publisher(event)
        return event

    async def disconnect(self, message: str = "账户已断开") -> dict[str, Any]:
        """Stop broker channels without closing the local order ledger."""
        async with self._connect_lock, self._operation_lock:
            self.account_transitioning = True
            self._positions_epoch += 1
            self._stop_positions_timer()
            self.disarm_live("账户已断开")
            self.connection_status = "connecting"
            self.connection_message = "正在安全断开券商API"
            await self.publisher(self.connection_event())
            try:
                await self.gateway.close()
            finally:
                self.account_transitioning = False
            self.connection_status = "offline"
            self.connection_message = message
            self.account_mask = ""
            self.usd_buying_power = None
            self.last_market_at = 0.0
            self.last_price = None
            self.quote = {}
            self.snapshot = {}
            self.ticks = []
            self.positions = []
            self.positions_updated_at = None
            self.positions_status = "not_connected"
            self.positions_message = "账户未连接"
            await self.publisher(self.positions_event())
            await self.publisher(self.risk_event())
            event = self.connection_event()
            await self.publisher(event)
            return event

    def set_credential_state(self, state: str) -> None:
        allowed = {"UNBOUND", "SESSION_ONLY", "CREDENTIALS_SAVED", "ENV_FALLBACK", "ERROR"}
        self.credential_state = state if state in allowed else "ERROR"

    async def switch_symbol(self, symbol: Any) -> dict[str, Any]:
        normalized = normalize_symbol(symbol)
        if not self.gateway.connected:
            raise RuntimeError("请先连接券商API")
        async with self._operation_lock, self._symbol_lock:
            if normalized == self.current_symbol:
                return {"type": "symbol.changed", "symbol": normalized}
            self.switching_symbol = True
            self.last_market_at = 0.0
            self._last_recovery_attempt = 0.0
            self.last_price = None
            self.quote = {}
            self.snapshot = {}
            self.ticks = []
            await self.publisher(self.connection_event())
            try:
                await self.gateway.switch_symbol(normalized)
                self.current_symbol = normalized
            finally:
                self.switching_symbol = False
            event = {"type": "symbol.changed", "symbol": normalized}
            await self.publisher(event)
            await self.publisher(self.connection_event())
            return event

    async def search_symbols(self, query: Any) -> dict[str, Any]:
        if not self.gateway.connected:
            raise RuntimeError("请先连接券商API后再搜索股票")
        normalized = normalize_instrument_query(query)
        items = await self.gateway.search_instruments(normalized)
        return {"type": "symbol.results", "query": normalized, "items": items}

    async def maintain_market_freshness(self, now: float | None = None) -> bool:
        """Confirm quiet streams through REST before the trading freshness window expires."""
        current = now or time.time()
        if (
            not self.gateway.connected
            or not self.gateway.stream_connected
            or not self.market_active
            or self.switching_symbol
        ):
            return False
        age = current - self.last_market_at if self.last_market_at else float("inf")
        # Quiet symbols get a confirmed refresh about every three seconds while
        # the page is visible. Streaming events naturally suppress these calls.
        recovery_after = max(2.0, min(3.0, self.settings.quote_stale_seconds * 0.45))
        retry_after = recovery_after
        if age < recovery_after or current - self._last_recovery_attempt < retry_after:
            return False
        if self._recovery_lock.locked():
            return False
        async with self._recovery_lock:
            # A streaming event may have arrived while this task was waiting.
            latest = time.time()
            latest_age = latest - self.last_market_at if self.last_market_at else float("inf")
            if latest_age < recovery_after:
                return False
            self._last_recovery_attempt = latest
            try:
                events = await self.gateway.recover_market(self.current_symbol)
            except Exception as exc:
                self._logger.warning("行情静默恢复校验失败: %s", exc)
                return False
            if not events:
                self._logger.warning("行情静默恢复校验未返回数据")
                return False
            for event in events:
                if str(event.get("symbol") or "").upper() != self.current_symbol:
                    continue
                self.receive_gateway_event(event)
            return bool(self.last_market_at and time.time() - self.last_market_at <= 1.5)

    async def set_market_active(self, active: bool) -> None:
        desired = bool(active)
        if desired == self.market_active:
            return
        async with self._operation_lock:
            if self.gateway.connected:
                await self.gateway.set_market_active(desired)
            self.market_active = desired
            self._last_recovery_attempt = 0.0
            if desired and self.gateway.connected:
                await self.maintain_market_freshness()
                await self.refresh_positions("foreground")
                self._start_positions_timer()
            elif not desired:
                self._stop_positions_timer()
        await self.publisher(self.connection_event())

    async def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = str(payload.get("requestId") or "").strip()
        if not request_id or len(request_id) > 80:
            raise ValueError("requestId 缺失或过长")
        payload_hash = self._payload_hash(payload)
        owner = False
        async with self._idempotency_lock:
            cached = self._request_results.get(request_id)
            if cached is not None:
                existing_hash = str(cached.get("order", {}).get("payloadHash") or payload_hash)
                if existing_hash != payload_hash:
                    raise ValueError("相同 requestId 的订单字段不一致")
                self._request_results.move_to_end(request_id)
                return cached
            future = self._request_futures.get(request_id)
            if future is not None:
                owner = False
            else:
                persisted = self.ledger.get_by_request(request_id)
                if persisted is not None:
                    if persisted.get("payloadHash") != payload_hash:
                        raise ValueError("相同 requestId 的订单字段不一致")
                    result = {"type": "order.update", "order": persisted}
                    self._remember_result(request_id, result)
                    return result
                future = asyncio.get_running_loop().create_future()
                self._request_futures[request_id] = future
                owner = True
        if not owner:
            return await asyncio.shield(future)
        try:
            result = await self._submit_once(payload, request_id, payload_hash)
            self._remember_result(request_id, result)
            future.set_result(result)
            return result
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)
                # Retrieve it here when there was no concurrent waiter.
                future.exception()
            raise
        finally:
            async with self._idempotency_lock:
                self._request_futures.pop(request_id, None)

    async def _submit_once(self, payload: dict[str, Any], request_id: str, payload_hash: str) -> dict[str, Any]:
        async with self._operation_lock:
            if not self.trading_enabled():
                raise RuntimeError(self.block_reason())
            symbol = normalize_symbol(payload.get("symbol"))
            if symbol != self.current_symbol:
                raise ValueError("订单股票与当前订阅股票不一致")
            side = str(payload.get("side") or "").upper()
            if side not in {"BUY", "SELL"}:
                raise ValueError("方向只能是 BUY 或 SELL")
            order_type = str(payload.get("orderType") or "").upper()
            if order_type not in {"MARKET", "LIMIT"}:
                raise ValueError("订单类型只能是 MARKET 或 LIMIT")
            quantity = self._positive_integer(payload.get("quantity"), "数量")
            if quantity > self.settings.max_order_qty:
                raise ValueError(f"数量超过单笔上限 {self.settings.max_order_qty}")
            limit_price: Decimal | None = None
            if order_type == "LIMIT":
                limit_price = self._positive_decimal(payload.get("limitPrice"), "限价")
            reference_price = limit_price or self.last_price
            if reference_price is None:
                raise RuntimeError("缺少可用于风控的实时价格")
            notional = reference_price * quantity
            max_value = Decimal(str(self.settings.max_order_value))
            if notional > max_value:
                raise ValueError(f"订单金额 {notional:.2f} 超过单笔上限 {self.settings.max_order_value:.2f}")
            if order_type == "MARKET":
                max_market_qty = Decimal(str(getattr(self.settings, "max_market_qty", self.settings.max_order_qty)))
                max_market_value = Decimal(str(getattr(self.settings, "max_market_estimated_value", self.settings.max_order_value)))
                if quantity > max_market_qty or notional > max_market_value:
                    raise ValueError("裸市价单超过独立数量或估算金额上限")
            if self.ledger.open_count() >= int(getattr(self.settings, "max_open_orders", 1)):
                raise RuntimeError("未决订单数量已达上限")
            daily_total = Decimal(str(self.ledger.estimated_notional_since(self._us_trade_day_start())))
            daily_limit = Decimal(str(getattr(self.settings, "max_daily_gross_notional", self.settings.max_order_value)))
            if daily_total + notional > daily_limit:
                raise ValueError("预计当日累计成交额超过上限")
            if side == "SELL":
                available = await self.gateway.get_available_position(symbol)
                if available is None or Decimal(str(available)) < quantity:
                    shown = 0 if available is None else available
                    raise ValueError(f"可卖持仓不足：可卖 {shown:g}，请求 {quantity}")
            if not self.trading_enabled() or symbol != self.current_symbol:
                raise RuntimeError(self.block_reason() or "交易状态在提交前发生变化")
            client_order_id = f"FO{time.time_ns():X}{uuid.uuid4().hex[:5]}"[:32]
            order = {
                "clientOrderId": client_order_id,
                "requestId": request_id,
                "payloadHash": payload_hash,
                "symbol": symbol,
                "side": side,
                "orderType": order_type,
                "quantity": int(quantity),
                "limitPrice": format(limit_price, "f") if limit_price is not None else None,
                "status": "PREPARED",
                "createdAt": time.time(),
            }
            order = self.ledger.prepare(order, request_id, payload_hash, format(reference_price, "f"), format(notional, "f"))
            self._upsert_order(order)
            order = self.ledger.update(client_order_id, "SENDING") or order
            self._upsert_order(order)
            await self.publisher({"type": "order.update", "order": dict(order)})
            try:
                raw = await self.gateway.place_order(order)
                order = self.ledger.update(client_order_id, "ACKNOWLEDGED", result=redact(raw)) or order
            except BrokerRejectedError as exc:
                order = self.ledger.update(client_order_id, "REJECTED", message=self._safe_error(exc)) or order
            except Exception as exc:
                order = await self._mark_and_reconcile_uncertain(client_order_id, exc)
            self._upsert_order(order)
            event = {"type": "order.update", "order": dict(order)}
            await self.publisher(event)
            return event

    async def cancel_order(self, client_order_id: Any) -> dict[str, Any]:
        value = str(client_order_id or "").strip()
        order = next((item for item in self.orders if item.get("clientOrderId") == value), None)
        if order is None:
            raise ValueError("找不到指定订单")
        status = self._normalize_status(order.get("status"))
        if status not in {"ACKNOWLEDGED", "SUBMITTED", "OPEN", "PARTIAL_FILLED", "CANCEL_REQUESTED"}:
            raise ValueError("该订单当前不可撤销")
        if status == "CANCEL_REQUESTED":
            return {"type": "order.update", "order": dict(order)}
        async with self._operation_lock:
            order = self.ledger.update(value, "CANCEL_REQUESTED", message="撤单请求已发送") or order
            self._upsert_order(order)
            await self.publisher({"type": "order.update", "order": dict(order)})
            try:
                result = await self.gateway.cancel_order(value)
                order = self.ledger.update(value, "CANCEL_REQUESTED", result=redact(result)) or order
            except BrokerRejectedError as exc:
                detail = await self._safe_order_detail(value)
                order = self._apply_detail(value, detail, fallback="OPEN", message=self._safe_error(exc))
            except Exception as exc:
                detail = await self._safe_order_detail(value)
                order = self._apply_detail(value, detail, fallback="UNKNOWN", message=f"撤单结果不确定：{self._safe_error(exc)}")
        event = {"type": "order.update", "order": dict(order)}
        await self.publisher(event)
        return event

    def arm_live(self) -> dict[str, Any]:
        if self.settings.sandbox:
            self.live_armed = True
            return self.risk_event()
        if not getattr(self.settings, "live_allowed", False):
            raise RuntimeError("配置未允许 LIVE 实盘")
        if not self.gateway.connected or self.connection_status != "connected" or not self.account_mask:
            raise RuntimeError("请先连接交易账户")
        self.live_armed = True
        return self.risk_event()

    def disarm_live(self, reason: str = "实盘已锁定") -> dict[str, Any]:
        if not self.settings.sandbox:
            self.live_armed = False
        self.connection_message = reason if reason else self.connection_message
        return self.risk_event()

    def risk_event(self) -> dict[str, Any]:
        return {
            "type": "risk.state",
            "sandbox": self.settings.sandbox,
            "liveArmed": self._live_is_armed(),
            "accountId": self.account_mask,
            "usdBuyingPower": self.usd_buying_power,
            "maxOrderQty": self.settings.max_order_qty,
            "maxOrderValue": self.settings.max_order_value,
            "maxMarketQty": getattr(self.settings, "max_market_qty", self.settings.max_order_qty),
            "maxMarketEstimatedValue": getattr(self.settings, "max_market_estimated_value", self.settings.max_order_value),
            "maxDailyGrossNotional": getattr(self.settings, "max_daily_gross_notional", self.settings.max_order_value),
            "maxOpenOrders": getattr(self.settings, "max_open_orders", 1),
        }

    async def reconcile_unresolved(self) -> None:
        for item in self.ledger.unresolved():
            client_id = str(item.get("clientOrderId") or "")
            if not client_id:
                continue
            detail = await self._safe_order_detail(client_id)
            if detail is None:
                self._unknown_order_ids.add(client_id)
                item = self.ledger.update(client_id, "UNKNOWN", message="启动对账尚未确认券商状态") or item
            else:
                item = self._apply_detail(client_id, detail, fallback="UNKNOWN", message="启动时已按 client_order_id 对账")
            self._upsert_order(item)

    def livez(self) -> dict[str, Any]:
        return {"ok": True, "status": "alive"}

    def readyz(self) -> dict[str, Any]:
        ready = bool(
            self.gateway.connected
            and self.gateway.stream_connected
            and getattr(self.gateway, "subscription_ready", self.gateway.stream_connected)
            and getattr(self.gateway, "events_connected", True)
            and not self._unknown_order_ids
        )
        return {"ok": ready, "status": self.connection_status, "symbol": self.current_symbol, "reason": "" if ready else self.block_reason()}

    def connection_event(self) -> dict[str, Any]:
        market_ready = bool(
            self.gateway.connected
            and self.gateway.stream_connected
            and getattr(self.gateway, "subscription_ready", self.gateway.stream_connected)
        )
        order_events_ready = bool(self.gateway.connected and getattr(self.gateway, "events_connected", True))
        trading_ready = self.trading_enabled()
        connection_state = {
            "offline": "DISCONNECTED",
            "connecting": "CONNECTING",
            "subscribing": "CONNECTING",
            "connected": "CONNECTED",
            "degraded": "CONNECTION_ERROR",
            "error": "CONNECTION_ERROR",
        }.get(self.connection_status, "DISCONNECTED")
        return {
            "type": "connection",
            "status": self.connection_status,
            "message": self.connection_message,
            "symbol": self.current_symbol,
            "sandbox": self.settings.sandbox,
            "credentialState": self.credential_state,
            "credentialsSaved": self.credential_state == "CREDENTIALS_SAVED",
            "connectionState": connection_state,
            "marketReady": market_ready,
            "orderEventsReady": order_events_ready,
            "tradingReady": trading_ready,
            "tradingEnabled": trading_ready,
            "blockReason": self.block_reason(),
            "lastMarketAt": self.last_market_at or None,
            "switching": self.switching_symbol,
            "accountTransitioning": self.account_transitioning,
            "marketActive": self.market_active,
            "subscriptionReady": getattr(self.gateway, "subscription_ready", self.gateway.stream_connected),
            "eventsConnected": getattr(self.gateway, "events_connected", True),
            "liveArmed": self._live_is_armed(),
            "accountId": self.account_mask,
        }

    def initial_state(self) -> dict[str, Any]:
        return {
            "type": "state",
            "connection": self.connection_event(),
            "symbol": self.current_symbol,
            "quote": self.quote,
            "snapshot": self.snapshot,
            "ticks": self.ticks,
            "orders": self.orders,
            "positions": self.positions_event(),
            "risk": self.risk_event(),
            "limits": {
                "maxQty": self.settings.max_order_qty,
                "maxValue": self.settings.max_order_value,
                "staleSeconds": self.settings.quote_stale_seconds,
            },
        }

    def trading_enabled(self, now: float | None = None) -> bool:
        current = now or time.time()
        return bool(
            self.gateway.connected
            and self.gateway.stream_connected
            and getattr(self.gateway, "subscription_ready", self.gateway.stream_connected)
            and getattr(self.gateway, "events_connected", True)
            and self.market_active
            and not self.account_transitioning
            and not self.switching_symbol
            and self._live_is_armed(current)
            and not self._unknown_order_ids
            and self.last_market_at
            and current - self.last_market_at <= self.settings.quote_stale_seconds
            and (self.settings.sandbox or self._in_core_session(current))
        )

    def block_reason(self, now: float | None = None) -> str:
        current = now or time.time()
        if not self.gateway.connected:
            return "交易账户未连接"
        if self.account_transitioning:
            return "账户连接正在安全切换"
        if not self.gateway.stream_connected:
            return "实时行情已断线"
        if not getattr(self.gateway, "subscription_ready", self.gateway.stream_connected):
            return "实时行情订阅尚未确认"
        if not getattr(self.gateway, "events_connected", True):
            return "订单状态链路已断线"
        if not self.market_active:
            return "页面在后台，行情刷新已暂停"
        if self.switching_symbol:
            return "正在切换股票"
        if not self.last_market_at:
            return "等待第一笔实时行情"
        if current - self.last_market_at > self.settings.quote_stale_seconds:
            return "实时行情已过期"
        if self._unknown_order_ids:
            return "存在结果不确定的订单，等待对账"
        if not self._live_is_armed(current):
            return "交易开关尚未开启"
        if not self.settings.sandbox and not self._in_core_session(current):
            return "当前不在美股常规交易时段"
        return ""

    async def close(self) -> None:
        self._positions_epoch += 1
        self._stop_positions_timer()
        await self.gateway.close()
        self.ledger.close()

    def _upsert_order(self, order: dict[str, Any]) -> None:
        existing = next((index for index, item in enumerate(self.orders) if item.get("clientOrderId") == order["clientOrderId"]), None)
        if existing is None:
            self.orders.insert(0, order)
        else:
            self.orders[existing] = order
        self.orders = self.orders[:50]

    def _remember_result(self, request_id: str, result: dict[str, Any]) -> None:
        self._request_results[request_id] = result
        self._request_results.move_to_end(request_id)
        while len(self._request_results) > 500:
            self._request_results.popitem(last=False)

    async def _mark_and_reconcile_uncertain(self, client_order_id: str, exc: BaseException) -> dict[str, Any]:
        self._unknown_order_ids.add(client_order_id)
        order = self.ledger.update(
            client_order_id,
            "UNKNOWN",
            message=f"提交结果不确定，未自动重发：{self._safe_error(exc)}",
        ) or {"clientOrderId": client_order_id, "status": "UNKNOWN"}
        detail = await self._safe_order_detail(client_order_id)
        if detail is not None:
            order = self._apply_detail(client_order_id, detail, fallback="UNKNOWN", message="已按 client_order_id 对账")
        return order

    async def _safe_order_detail(self, client_order_id: str) -> Any | None:
        try:
            return await self.gateway.get_order_detail(client_order_id)
        except Exception as exc:
            self._logger.warning("订单对账暂时失败 (%s): %s", client_order_id[-6:], type(exc).__name__)
            return None

    def _apply_detail(self, client_order_id: str, detail: Any, *, fallback: str, message: str) -> dict[str, Any]:
        if not isinstance(detail, dict):
            return self.ledger.update(client_order_id, fallback, message=message) or {"clientOrderId": client_order_id, "status": fallback}
        detail_id = str(detail.get("client_order_id") or detail.get("clientOrderId") or client_order_id)
        if detail_id != client_order_id:
            return self.ledger.update(client_order_id, "UNKNOWN", message="券商对账返回的订单标识不一致") or {"clientOrderId": client_order_id, "status": "UNKNOWN"}
        status = self._normalize_status(detail.get("status") or detail.get("order_status") or detail.get("state") or fallback)
        order = self.ledger.update(client_order_id, status, result=redact(detail), message=message) or {"clientOrderId": client_order_id, "status": status}
        if status not in {"PREPARED", "SENDING", "UNKNOWN"}:
            self._unknown_order_ids.discard(client_order_id)
        return order

    def _live_is_armed(self, now: float | None = None) -> bool:
        del now
        if self.settings.sandbox:
            return True
        return self.live_armed

    @staticmethod
    def _normalize_status(value: Any) -> str:
        status = str(value or "UNKNOWN").strip().upper()
        return STATUS_ALIASES.get(status, status)

    @staticmethod
    def _payload_hash(payload: dict[str, Any]) -> str:
        normalized = {
            "symbol": str(payload.get("symbol") or "").strip().upper(),
            "side": str(payload.get("side") or "").strip().upper(),
            "orderType": str(payload.get("orderType") or "").strip().upper(),
            "quantity": str(payload.get("quantity") or "").strip(),
            "limitPrice": str(payload.get("limitPrice") or "").strip(),
        }
        encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _safe_error(exc: BaseException) -> str:
        if isinstance(exc, BrokerRejectedError):
            return redact_text(exc)
        return type(exc).__name__

    @staticmethod
    def _in_core_session(timestamp: float) -> bool:
        eastern = datetime.fromtimestamp(timestamp, ZoneInfo("America/New_York"))
        if eastern.weekday() >= 5:
            return False
        return clock_time(9, 30) <= eastern.time().replace(tzinfo=None) < clock_time(16, 0)

    @staticmethod
    def _us_trade_day_start() -> float:
        eastern_zone = ZoneInfo("America/New_York")
        now = datetime.now(eastern_zone)
        start = datetime.combine(now.date(), clock_time.min, tzinfo=eastern_zone)
        return start.timestamp()

    @staticmethod
    def _positive_integer(value: Any, label: str) -> Decimal:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError(f"{label}必须是正整数") from None
        if number <= 0 or number != number.to_integral_value():
            raise ValueError(f"{label}必须是正整数")
        return number

    @staticmethod
    def _positive_decimal(value: Any, label: str) -> Decimal:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError(f"{label}必须大于 0") from None
        if number <= 0 or not number.is_finite():
            raise ValueError(f"{label}必须大于 0")
        return number

    @staticmethod
    def _extract_price(kind: str, data: dict[str, Any]) -> Decimal | None:
        keys = {"price", "last_price", "last", "close"}
        if kind == "quote":
            asks = data.get("asks") if isinstance(data.get("asks"), list) else []
            bids = data.get("bids") if isinstance(data.get("bids"), list) else []
            values = []
            for level in (asks[:1] + bids[:1]):
                found = find_number(level, {"price"})
                if found and found > 0:
                    values.append(found)
            price = sum(values) / len(values) if values else find_number(data, keys)
        else:
            price = find_number(data, keys)
        if price is None or price <= 0:
            return None
        return Decimal(str(price))

    @staticmethod
    def _public_event(event: dict[str, Any]) -> dict[str, Any]:
        return redact(event)

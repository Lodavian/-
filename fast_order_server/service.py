from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol
from zoneinfo import ZoneInfo

from fast_order.errors import BrokerRejectedError, OrderUncertainError
from fast_order.service import STATUS_ALIASES, STATUS_RANK
from fast_order.utils import normalize_symbol, redact

from .controls import ControlPlane
from .models import Principal, RiskLimits, ServerOrder
from .store import OrderStore


class InstitutionalGateway(Protocol):
    events_healthy: bool
    async def fresh_price(self, symbol: str, max_age: float) -> Decimal | None: ...
    async def get_available_position(self, account_id: str, symbol: str) -> Decimal | None: ...
    async def place_order(self, account_id: str, order: dict[str, Any]) -> Any: ...
    async def replace_order(self, account_id: str, order: dict[str, Any]) -> Any: ...
    async def cancel_order(self, account_id: str, client_order_id: str) -> Any: ...
    async def get_order_detail(self, account_id: str, client_order_id: str) -> Any | None: ...


Publisher = Callable[[dict[str, Any]], Awaitable[None]]


class InstitutionalTradingService:
    """Multi-user control and account routing around the existing order rules."""

    def __init__(
        self,
        controls: ControlPlane,
        store: OrderStore,
        gateway: InstitutionalGateway,
        *,
        limits: RiskLimits | None = None,
        publisher: Publisher | None = None,
    ):
        self.controls = controls
        self.store = store
        self.gateway = gateway
        self.limits = limits or RiskLimits()
        self.publisher = publisher or self._discard
        self._account_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._reconciliation_blocked: set[str] = set()

    @property
    def reconciliation_ready(self) -> bool:
        return not (self._reconciliation_blocked & set(self.controls.snapshot.accounts))

    @staticmethod
    async def _discard(_event: dict[str, Any]) -> None:
        return None

    async def list_accounts(self, principal: Principal) -> list[dict[str, object]]:
        return self.controls.public_accounts(principal)

    async def list_orders(self, principal: Principal, account_ref: str | None = None, limit: int = 50, before: tuple[float, str] | None = None) -> tuple[list[dict[str, Any]], tuple[float, str] | None]:
        if account_ref:
            self.controls.resolve(principal, account_ref, mutation="read")
        rows = await self.store.recent(principal.user_id, account_ref, limit, before)
        next_cursor = (rows[-1].created_at, rows[-1].client_order_id) if len(rows) == max(1, min(limit, 200)) else None
        return [item.public() for item in rows], next_cursor

    async def get_order(self, principal: Principal, account_ref: str, client_order_id: str) -> dict[str, Any]:
        self.controls.resolve(principal, account_ref, mutation="read")
        order = await self.store.find(account_ref, client_order_id)
        if order is None or order.user_id != principal.user_id:
            raise ValueError("找不到指定订单")
        return order.public()

    async def get_request(self, principal: Principal, account_ref: str, request_id: str) -> dict[str, Any]:
        self.controls.resolve(principal, account_ref, mutation="read")
        found = await self.store.find_request(account_ref, request_id)
        if found is None or found[0].user_id != principal.user_id:
            raise ValueError("找不到指定幂等请求")
        return self._event(found[0])

    async def submit(self, principal: Principal, payload: dict[str, Any], request_id: str) -> dict[str, Any]:
        account_ref = str(payload.get("accountRef") or "")
        account = self.controls.resolve(principal, account_ref, mutation="submit")
        if account_ref in self._reconciliation_blocked:
            raise RuntimeError("账户存在未完成对账，禁止新单")
        normalized = await self._validate_new(account_ref, payload)
        payload_hash = self._payload_hash(normalized)
        async with self._account_locks[account_ref]:
            account = self.controls.resolve(principal, account_ref, mutation="submit")
            if account_ref in self._reconciliation_blocked:
                raise RuntimeError("账户存在未完成对账，禁止新单")
            existing_request = await self.store.find_request(account_ref, request_id)
            if existing_request is not None:
                existing, existing_hash = existing_request
                if existing_hash != payload_hash:
                    raise ValueError("相同 Idempotency-Key 的订单字段不一致")
                return self._event(existing)
            await self._check_account_limits(account_ref, normalized)
            now = time.time()
            order = ServerOrder(
                account_ref=account_ref,
                user_id=principal.user_id,
                device_id=principal.device_id,
                request_id=request_id,
                client_order_id=self._client_order_id(),
                status="RECEIVED",
                created_at=now,
                updated_at=now,
                payload_hash=payload_hash,
                **normalized,
            )
            order = await self.store.insert(order)
            await self._publish(order)
            order = await self.store.update(order, "PREPARED")
            await self._publish(order)
            order = await self.store.update(order, "SENDING")
            await self._publish(order)
            try:
                result = await self.gateway.place_order(account.broker_account_id, self._broker_order(order))
                order = await self.store.update(order, "ACKNOWLEDGED", result=redact(result))
            except BrokerRejectedError as exc:
                order = await self.store.update(order, "REJECTED", message=self._safe_error(exc))
            except Exception as exc:
                order = await self._uncertain(account.broker_account_id, order, exc)
            await self.store.audit("order.submit.result", order, order.status, order.message)
            await self._publish(order)
            return self._event(order)

    async def replace_limit(
        self,
        principal: Principal,
        account_ref: str,
        client_order_id: str,
        limit_price: Any,
        request_id: str,
    ) -> dict[str, Any]:
        account = self.controls.resolve(principal, account_ref, mutation="replace")
        if account_ref in self._reconciliation_blocked:
            raise RuntimeError("账户存在未完成对账，禁止改单")
        price = self._positive_decimal(limit_price, "限价")
        action_hash = self._payload_hash(
            {"action": "replace", "clientOrderId": client_order_id, "limitPrice": format(price, "f")}
        )
        async with self._account_locks[account_ref]:
            account = self.controls.resolve(principal, account_ref, mutation="replace")
            if account_ref in self._reconciliation_blocked:
                raise RuntimeError("账户存在未完成对账，禁止改单")
            duplicate_request = await self.store.find_request(account_ref, request_id)
            if duplicate_request is not None:
                duplicate, existing_hash = duplicate_request
                if existing_hash != action_hash:
                    raise ValueError("相同 Idempotency-Key 的操作字段不一致")
                return self._event(duplicate)
            order = await self.store.find(account_ref, client_order_id)
            if order is None or order.user_id != principal.user_id:
                raise ValueError("找不到指定订单")
            if order.order_type != "LIMIT" or order.status not in {"ACKNOWLEDGED", "SUBMITTED", "OPEN", "PARTIAL_FILLED", "REPLACED"}:
                raise ValueError("该订单当前不可修改限价")
            notional = price * order.quantity
            if notional > self.limits.max_order_value:
                raise ValueError("修改后的订单金额超过单笔上限")
            if not self.gateway.events_healthy:
                raise RuntimeError("订单事件通道不可用，禁止改单")
            await self._check_replace_limits(account_ref, order, notional)
            await self.store.remember_request(order, request_id, action_hash)
            await self.store.audit("order.replace", order, "RECEIVED")
            previous_price = order.limit_price
            previous_reference = order.reference_price
            previous_notional = order.estimated_notional
            order.limit_price = format(price, "f")
            order.reference_price = format(price, "f")
            order.estimated_notional = format(notional, "f")
            order = await self.store.update(order, "REPLACE_REQUESTED")
            await self._publish(order)
            try:
                result = await self.gateway.replace_order(account.broker_account_id, self._broker_order(order))
                order = await self.store.update(order, "REPLACED", result=redact(result))
            except BrokerRejectedError as exc:
                order.limit_price = previous_price
                order.reference_price = previous_reference
                order.estimated_notional = previous_notional
                order = await self.store.update(order, "SUBMITTED", message=self._safe_error(exc))
            except Exception as exc:
                order = await self._uncertain(account.broker_account_id, order, exc)
            await self.store.audit("order.replace.result", order, order.status, order.message)
            await self._publish(order)
            return self._event(order)

    async def receive_broker_event(self, event: dict[str, Any]) -> None:
        candidates = list(self._dicts(event.get("data", event)))
        data = next(
            (item for item in candidates if item.get("client_order_id") or item.get("clientOrderId")),
            {},
        )
        account_id = str(data.get("account_id") or data.get("accountId") or "")
        client_order_id = str(data.get("client_order_id") or data.get("clientOrderId") or "")
        if not account_id or not client_order_id:
            return
        account = next(
            (item for item in self.controls.snapshot.accounts.values() if item.broker_account_id == account_id), None
        )
        if account is None:
            return
        async with self._account_locks[account.account_ref]:
            order = await self.store.find(account.account_ref, client_order_id)
            if order is None:
                return
            raw = str(data.get("status") or data.get("order_status") or data.get("state") or "UNKNOWN").upper()
            status = STATUS_ALIASES.get(raw, raw)
            scene = str(data.get("scene_type") or data.get("sceneType") or "").upper()
            if scene == "MODIFY_SUCCESS":
                status = "REPLACED"
            elif scene == "MODIFY_FAILED" and order.status == "REPLACE_REQUESTED":
                status = "SUBMITTED"
            elif scene == "CANCEL_FAILED" and order.status == "CANCEL_REQUESTED":
                status = "OPEN"
            if status in {"REPLACED", "SUBMITTED", "OPEN"} or STATUS_RANK.get(status, 4) >= STATUS_RANK.get(order.status, 0):
                order = await self.store.update(order, status, result=redact(data))
                if status != "UNKNOWN":
                    self._reconciliation_blocked.discard(account.account_ref)
                await self._publish(order)

    async def reconcile_unresolved(self) -> None:
        blocked: set[str] = set()
        for order in await self.store.unresolved():
            account = self.controls.snapshot.accounts.get(order.account_ref)
            if account is None:
                await self.store.update(order, "UNKNOWN", message="账户绑定不存在，无法完成启动对账")
                blocked.add(order.account_ref)
                continue
            try:
                detail = await self.gateway.get_order_detail(account.broker_account_id, order.client_order_id)
            except Exception:
                detail = None
            if not isinstance(detail, dict):
                await self.store.update(order, "UNKNOWN", message="启动对账尚未确认券商状态")
                blocked.add(order.account_ref)
                continue
            raw = str(detail.get("status") or detail.get("order_status") or detail.get("state") or "UNKNOWN").upper()
            status = STATUS_ALIASES.get(raw, raw)
            await self.store.update(order, status, result=redact(detail), message="启动时已按 client_order_id 对账")
            if status == "UNKNOWN":
                blocked.add(order.account_ref)
        self._reconciliation_blocked = blocked

    async def cancel(self, principal: Principal, account_ref: str, client_order_id: str, request_id: str) -> dict[str, Any]:
        account = self.controls.resolve(principal, account_ref, mutation="cancel")
        action_hash = self._payload_hash({"action": "cancel", "clientOrderId": client_order_id})
        async with self._account_locks[account_ref]:
            account = self.controls.resolve(principal, account_ref, mutation="cancel")
            duplicate_request = await self.store.find_request(account_ref, request_id)
            if duplicate_request is not None:
                duplicate, existing_hash = duplicate_request
                if existing_hash != action_hash:
                    raise ValueError("相同 Idempotency-Key 的操作字段不一致")
                return self._event(duplicate)
            order = await self.store.find(account_ref, client_order_id)
            if order is None or order.user_id != principal.user_id:
                raise ValueError("找不到指定订单")
            if order.status not in {"ACKNOWLEDGED", "SUBMITTED", "OPEN", "PARTIAL_FILLED", "REPLACED", "CANCEL_REQUESTED"}:
                raise ValueError("该订单当前不可撤销")
            await self.store.remember_request(order, request_id, action_hash)
            await self.store.audit("order.cancel", order, "RECEIVED")
            if order.status != "CANCEL_REQUESTED":
                order = await self.store.update(order, "CANCEL_REQUESTED")
                await self._publish(order)
            try:
                result = await self.gateway.cancel_order(account.broker_account_id, client_order_id)
                order = await self.store.update(order, "CANCEL_REQUESTED", result=redact(result))
            except BrokerRejectedError as exc:
                order = await self.store.update(order, "OPEN", message=self._safe_error(exc))
            except Exception as exc:
                order = await self._uncertain(account.broker_account_id, order, exc)
            await self.store.audit("order.cancel.result", order, order.status, order.message)
            await self._publish(order)
            return self._event(order)

    async def _validate_new(self, account_ref: str, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {"accountRef", "symbol", "side", "orderType", "quantity", "limitPrice"}
        extra = set(payload) - allowed
        if extra:
            raise ValueError(f"订单包含禁止字段: {', '.join(sorted(extra))}")
        symbol = normalize_symbol(payload.get("symbol"))
        side = str(payload.get("side") or "").upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("方向只能是 BUY 或 SELL")
        order_type = str(payload.get("orderType") or "").upper()
        if order_type not in {"MARKET", "LIMIT"}:
            raise ValueError("订单类型只能是 MARKET 或 LIMIT")
        quantity = self._positive_int(payload.get("quantity"), "数量")
        if quantity > self.limits.max_order_qty:
            raise ValueError("数量超过单笔上限")
        limit_price = self._positive_decimal(payload.get("limitPrice"), "限价") if order_type == "LIMIT" else None
        reference = limit_price or await self.gateway.fresh_price(symbol, self.limits.quote_stale_seconds)
        if reference is None:
            raise RuntimeError("缺少新鲜行情，禁止下单")
        notional = reference * quantity
        if notional > self.limits.max_order_value:
            raise ValueError("订单金额超过单笔上限")
        if order_type == "MARKET" and (quantity > self.limits.max_market_qty or notional > self.limits.max_market_estimated_value):
            raise ValueError("裸市价单超过独立数量或估算金额上限")
        if side == "SELL":
            account = self.controls.snapshot.accounts[account_ref]
            available = await self.gateway.get_available_position(account.broker_account_id, symbol)
            if available is None or available < quantity:
                raise ValueError("可卖持仓不足")
        if not self.gateway.events_healthy:
            raise RuntimeError("订单事件通道不可用，禁止下单")
        return {
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "quantity": quantity,
            "limit_price": format(limit_price, "f") if limit_price is not None else None,
            "reference_price": format(reference, "f"),
            "estimated_notional": format(notional, "f"),
        }

    async def _check_account_limits(self, account_ref: str, normalized: dict[str, Any]) -> None:
        if await self.store.open_count(account_ref) >= self.limits.max_open_orders:
            raise RuntimeError("未决订单数量已达上限")
        day_start = self._us_trading_day_start()
        daily = Decimal(str(await self.store.daily_notional(account_ref, day_start)))
        if daily + Decimal(normalized["estimated_notional"]) > self.limits.max_daily_gross_notional:
            raise ValueError("预计当日累计成交额超过上限")

    async def _check_replace_limits(self, account_ref: str, order: ServerOrder, replacement: Decimal) -> None:
        daily = Decimal(str(await self.store.daily_notional(account_ref, self._us_trading_day_start())))
        original = Decimal(order.estimated_notional)
        if max(Decimal("0"), daily - original) + replacement > self.limits.max_daily_gross_notional:
            raise ValueError("修改后预计当日累计成交额超过上限")

    @staticmethod
    def _us_trading_day_start() -> float:
        eastern = ZoneInfo("America/New_York")
        now = datetime.now(timezone.utc).astimezone(eastern)
        return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    @staticmethod
    def _dicts(value: Any):
        if isinstance(value, dict):
            yield value
            for nested in value.values():
                yield from InstitutionalTradingService._dicts(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from InstitutionalTradingService._dicts(nested)

    async def _uncertain(self, broker_account_id: str, order: ServerOrder, exc: Exception) -> ServerOrder:
        try:
            detail = await self.gateway.get_order_detail(broker_account_id, order.client_order_id)
        except Exception:
            detail = None
        if detail:
            return await self.store.update(order, "SUBMITTED", result=redact(detail), message="已按 client_order_id 对账")
        return await self.store.update(order, "UNKNOWN", message=f"结果不确定：{self._safe_error(exc)}")

    async def _publish(self, order: ServerOrder) -> None:
        event = self._event(order)
        event["_userId"] = order.user_id
        await self.publisher(event)

    @staticmethod
    def _event(order: ServerOrder) -> dict[str, Any]:
        public = order.public()
        return {
            "type": "order.update",
            "eventId": str(uuid.uuid4()),
            "occurredAt": datetime.now(timezone.utc).isoformat(),
            **public,
            "order": public,
        }

    @staticmethod
    def _broker_order(order: ServerOrder) -> dict[str, Any]:
        return {
            "clientOrderId": order.client_order_id,
            "symbol": order.symbol,
            "side": order.side,
            "orderType": order.order_type,
            "quantity": order.quantity,
            "limitPrice": order.limit_price,
        }

    @staticmethod
    def _client_order_id() -> str:
        return f"FO{time.time_ns():X}{uuid.uuid4().hex[:5]}"[:32]

    @staticmethod
    def _payload_hash(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _positive_int(value: Any, name: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{name}必须是正整数")
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError(f"{name}必须是正整数") from None
        if not number.is_finite() or number <= 0 or number != number.to_integral_value():
            raise ValueError(f"{name}必须是正整数")
        return int(number)

    @staticmethod
    def _positive_decimal(value: Any, name: str) -> Decimal:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError(f"{name}必须是正数") from None
        if not number.is_finite() or number <= 0:
            raise ValueError(f"{name}必须是正数")
        return number

    @staticmethod
    def _safe_error(exc: BaseException) -> str:
        if isinstance(exc, (BrokerRejectedError, OrderUncertainError, ValueError, RuntimeError)):
            text = str(exc).strip()
            if text and len(text) <= 160:
                return text
        return exc.__class__.__name__

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Protocol

from .models import ServerOrder


ACTIVE_STATUSES = {
    "RECEIVED", "PREPARED", "SENDING", "ACKNOWLEDGED", "SUBMITTED",
    "OPEN", "PARTIAL_FILLED", "REPLACE_REQUESTED", "CANCEL_REQUESTED", "UNKNOWN",
}


class OrderStore(Protocol):
    async def find_request(self, account_ref: str, request_id: str) -> tuple[ServerOrder, str] | None: ...
    async def find(self, account_ref: str, client_order_id: str) -> ServerOrder | None: ...
    async def insert(self, order: ServerOrder) -> ServerOrder: ...
    async def remember_request(self, order: ServerOrder, request_id: str, payload_hash: str) -> None: ...
    async def update(self, order: ServerOrder, status: str, *, message: str = "", result: object = None) -> ServerOrder: ...
    async def recent(self, user_id: str, account_ref: str | None, limit: int = 50, before: tuple[float, str] | None = None) -> list[ServerOrder]: ...
    async def open_count(self, account_ref: str) -> int: ...
    async def daily_notional(self, account_ref: str, since: float) -> float: ...
    async def unresolved(self) -> list[ServerOrder]: ...
    async def audit(self, action: str, order: ServerOrder, outcome: str, reason: str = "") -> None: ...


class MemoryOrderStore:
    """Deterministic store for unit tests and local API smoke tests."""

    def __init__(self):
        self._orders: dict[tuple[str, str], ServerOrder] = {}
        self._requests: dict[tuple[str, str], tuple[str, str]] = {}
        self._lock = asyncio.Lock()

    async def find_request(self, account_ref: str, request_id: str) -> tuple[ServerOrder, str] | None:
        found = self._requests.get((account_ref, request_id))
        if not found:
            return None
        order = self._orders.get((account_ref, found[0]))
        return (order, found[1]) if order is not None else None

    async def find(self, account_ref: str, client_order_id: str) -> ServerOrder | None:
        return self._orders.get((account_ref, client_order_id))

    async def insert(self, order: ServerOrder) -> ServerOrder:
        async with self._lock:
            request_key = (order.account_ref, order.request_id)
            existing = self._requests.get(request_key)
            if existing:
                return self._orders[(order.account_ref, existing[0])]
            self._requests[request_key] = (order.client_order_id, order.payload_hash)
            self._orders[(order.account_ref, order.client_order_id)] = order
            return order

    async def remember_request(self, order: ServerOrder, request_id: str, payload_hash: str) -> None:
        async with self._lock:
            key = (order.account_ref, request_id)
            existing = self._requests.get(key)
            if existing and existing != (order.client_order_id, payload_hash):
                raise ValueError("相同 Idempotency-Key 的操作字段不一致")
            self._requests[key] = (order.client_order_id, payload_hash)

    async def update(self, order: ServerOrder, status: str, *, message: str = "", result: object = None) -> ServerOrder:
        async with self._lock:
            order.status = status
            order.version += 1
            order.updated_at = time.time()
            if message:
                order.message = message
            if result is not None:
                order.result = result
            return order

    async def recent(self, user_id: str, account_ref: str | None, limit: int = 50, before: tuple[float, str] | None = None) -> list[ServerOrder]:
        rows = [
            item for item in self._orders.values()
            if item.user_id == user_id and (account_ref is None or item.account_ref == account_ref)
            and (before is None or (item.created_at, item.client_order_id) < before)
        ]
        rows.sort(key=lambda item: (item.created_at, item.client_order_id), reverse=True)
        return rows[: max(1, min(limit, 200))]

    async def open_count(self, account_ref: str) -> int:
        return sum(
            item.account_ref == account_ref and item.status in ACTIVE_STATUSES
            for item in self._orders.values()
        )

    async def daily_notional(self, account_ref: str, since: float) -> float:
        return sum(
            float(item.estimated_notional)
            for item in self._orders.values()
            if item.account_ref == account_ref and item.created_at >= since and item.status != "REJECTED"
        )

    async def unresolved(self) -> list[ServerOrder]:
        return [item for item in self._orders.values() if item.status in ACTIVE_STATUSES]

    async def audit(self, action: str, order: ServerOrder, outcome: str, reason: str = "") -> None:
        return None

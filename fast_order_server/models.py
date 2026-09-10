from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    device_id: str


@dataclass(frozen=True, slots=True)
class UserGrant:
    user_id: str
    enabled: bool = True
    trade_enabled: bool = True


@dataclass(frozen=True, slots=True)
class DeviceGrant:
    device_id: str
    user_id: str
    public_key_pem: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class AccountGrant:
    account_ref: str
    owner_user_id: str
    broker_account_id: str
    alias: str
    account_mask: str
    enabled: bool = True
    trade_enabled: bool = True


@dataclass(slots=True)
class ServerOrder:
    account_ref: str
    user_id: str
    device_id: str
    request_id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: int
    limit_price: str | None
    reference_price: str
    estimated_notional: str
    status: str
    version: int = 1
    message: str = ""
    result: Any = None
    created_at: float = 0.0
    updated_at: float = 0.0
    payload_hash: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "accountRef": self.account_ref,
            "clientOrderId": self.client_order_id,
            "requestId": self.request_id,
            "status": self.status,
            "symbol": self.symbol,
            "side": self.side,
            "orderType": self.order_type,
            "quantity": self.quantity,
            "limitPrice": self.limit_price,
            "message": self.message,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_order_qty: int = 1_000
    max_order_value: Decimal = field(default_factory=lambda: Decimal("25000"))
    max_market_qty: int = 100
    max_market_estimated_value: Decimal = field(default_factory=lambda: Decimal("5000"))
    max_daily_gross_notional: Decimal = field(default_factory=lambda: Decimal("25000"))
    max_open_orders: int = 1
    quote_stale_seconds: float = 8.0

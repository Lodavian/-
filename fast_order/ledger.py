from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


ACTIVE_STATUSES = {
    "PREPARED",
    "SENDING",
    "UNKNOWN",
    "ACKNOWLEDGED",
    "SUBMITTED",
    "OPEN",
    "PARTIAL_FILLED",
    "CANCEL_REQUESTED",
}


class OrderLedger:
    """Small crash-safe SQLite order and idempotency journal."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    client_order_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    payload_hash TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    limit_price TEXT,
                    reference_price TEXT NOT NULL,
                    estimated_notional TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    message TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            self._db.execute("CREATE INDEX IF NOT EXISTS orders_status_idx ON orders(status, updated_at)")
            self._db.commit()

    def prepare(self, order: dict[str, Any], request_id: str, payload_hash: str, reference_price: str, notional: str) -> dict[str, Any]:
        now = float(order.get("createdAt") or time.time())
        with self._lock:
            existing = self._db.execute("SELECT * FROM orders WHERE request_id = ?", (request_id,)).fetchone()
            if existing is not None:
                item = self._row(existing)
                if item["payloadHash"] != payload_hash:
                    raise ValueError("相同 requestId 的订单字段不一致")
                return item
            self._db.execute(
                """
                INSERT INTO orders (
                    client_order_id, request_id, payload_hash, symbol, side, order_type,
                    quantity, limit_price, reference_price, estimated_notional, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PREPARED', ?, ?)
                """,
                (
                    order["clientOrderId"], request_id, payload_hash, order["symbol"], order["side"],
                    order["orderType"], int(order["quantity"]), order.get("limitPrice"),
                    reference_price, notional, now, now,
                ),
            )
            self._db.commit()
            return self.get_by_request(request_id) or dict(order)

    def update(self, client_order_id: str, status: str, *, result: Any = None, message: str | None = None) -> dict[str, Any] | None:
        encoded = json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":")) if result is not None else None
        with self._lock:
            self._db.execute(
                "UPDATE orders SET status = ?, result_json = COALESCE(?, result_json), message = COALESCE(?, message), updated_at = ? WHERE client_order_id = ?",
                (status, encoded, message, time.time(), client_order_id),
            )
            self._db.commit()
            row = self._db.execute("SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)).fetchone()
            return self._row(row) if row is not None else None

    def get_by_request(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM orders WHERE request_id = ?", (request_id,)).fetchone()
            return self._row(row) if row is not None else None

    def get(self, client_order_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)).fetchone()
            return self._row(row) if row is not None else None

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
            return [self._row(row) for row in rows]

    def unresolved(self) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in ACTIVE_STATUSES)
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM orders WHERE status IN ({marks}) ORDER BY created_at",
                tuple(sorted(ACTIVE_STATUSES)),
            ).fetchall()
            return [self._row(row) for row in rows]

    def open_count(self) -> int:
        return len(self.unresolved())

    def estimated_notional_since(self, timestamp: float) -> float:
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(SUM(CAST(estimated_notional AS REAL)), 0) AS total FROM orders WHERE created_at >= ? AND status != 'REJECTED'",
                (timestamp,),
            ).fetchone()
            return float(row["total"] or 0.0)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        result = None
        if row["result_json"]:
            try:
                result = json.loads(row["result_json"])
            except (TypeError, json.JSONDecodeError):
                result = None
        return {
            "clientOrderId": row["client_order_id"],
            "requestId": row["request_id"],
            "payloadHash": row["payload_hash"],
            "symbol": row["symbol"],
            "side": row["side"],
            "orderType": row["order_type"],
            "quantity": row["quantity"],
            "limitPrice": row["limit_price"],
            "referencePrice": row["reference_price"],
            "estimatedNotional": row["estimated_notional"],
            "status": row["status"],
            "result": result,
            "message": row["message"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

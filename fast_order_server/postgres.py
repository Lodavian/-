from __future__ import annotations

import json
import hashlib
import hmac
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .controls import ControlSnapshot
from .models import AccountGrant, DeviceGrant, ServerOrder, UserGrant
from .store import ACTIVE_STATUSES


class AccountCipher:
    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("账户加密主密钥必须是32字节")
        self._cipher = AESGCM(key)
        self._key = key

    def encrypt(self, account_id: str) -> bytes:
        import os

        nonce = os.urandom(12)
        return nonce + self._cipher.encrypt(nonce, account_id.encode("utf-8"), b"FastOrder/account-id/v1")

    def decrypt(self, value: bytes) -> str:
        raw = bytes(value)
        if len(raw) < 29:
            raise ValueError("账户密文损坏")
        return self._cipher.decrypt(raw[:12], raw[12:], b"FastOrder/account-id/v1").decode("utf-8")

    def fingerprint(self, account_id: str) -> str:
        """Stable, non-reversible identifier used only to reject duplicate account rows."""
        return hmac.new(self._key, account_id.encode("utf-8"), hashlib.sha256).hexdigest()


class PostgresStore:
    def __init__(self, pool: Any, cipher: AccountCipher):
        self.pool = pool
        self.cipher = cipher

    @classmethod
    async def connect(cls, dsn: str, cipher: AccountCipher) -> "PostgresStore":
        try:
            import asyncpg
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("asyncpg未安装") from exc
        pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=8, command_timeout=10,
            server_settings={"search_path": "fastorder", "application_name": "fastorder-server"},
        )
        return cls(pool, cipher)

    async def migrate(self) -> None:
        sql = (Path(__file__).with_name("migrations") / "001_initial.sql").read_text(encoding="utf-8")
        async with self.pool.acquire() as connection:
            await connection.execute(sql)

    async def close(self) -> None:
        await self.pool.close()

    async def healthy(self) -> bool:
        try:
            return int(await self.pool.fetchval("SELECT 1")) == 1
        except Exception:
            return False

    async def load_controls(self, *, force_global_off: bool = False) -> ControlSnapshot:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                if force_global_off:
                    await connection.execute(
                        "UPDATE control_state SET global_trade_enabled=FALSE, version=version+1, "
                        "updated_at=now(), updated_by='bootstrap', reason='服务重启后默认关闭交易' WHERE singleton=TRUE"
                    )
                state = await connection.fetchrow("SELECT global_trade_enabled, version FROM control_state WHERE singleton=TRUE")
                users = await connection.fetch("SELECT user_id, enabled, trade_enabled FROM app_users")
                devices = await connection.fetch("SELECT device_id, user_id, public_key_pem, enabled FROM devices")
                accounts = await connection.fetch(
                    "SELECT a.account_ref, a.broker_account_cipher, a.account_mask, a.alias, a.enabled, "
                    "a.trade_enabled, b.user_id FROM broker_accounts a "
                    "JOIN user_account_bindings b ON b.account_ref=a.account_ref"
                )
        return ControlSnapshot.frozen(
            global_trade_enabled=bool(state["global_trade_enabled"]),
            version=int(state["version"]),
            users={
                str(row["user_id"]): UserGrant(str(row["user_id"]), bool(row["enabled"]), bool(row["trade_enabled"]))
                for row in users
            },
            devices={
                str(row["device_id"]): DeviceGrant(str(row["device_id"]), str(row["user_id"]), str(row["public_key_pem"]), bool(row["enabled"]))
                for row in devices
            },
            accounts={
                str(row["account_ref"]): AccountGrant(
                    str(row["account_ref"]), str(row["user_id"]),
                    self.cipher.decrypt(row["broker_account_cipher"]), str(row["alias"]),
                    str(row["account_mask"]), bool(row["enabled"]), bool(row["trade_enabled"]),
                )
                for row in accounts
            },
        )

    async def set_control(self, scope: str, key: str | None, enabled: bool, actor: str, reason: str) -> ControlSnapshot:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                if scope == "global":
                    await connection.execute(
                        "UPDATE control_state SET global_trade_enabled=$1, version=version+1, updated_at=now(), updated_by=$2, reason=$3 WHERE singleton=TRUE",
                        enabled, actor, reason,
                    )
                elif scope == "user":
                    result = await connection.execute("UPDATE app_users SET trade_enabled=$1, updated_at=now() WHERE user_id=$2", enabled, key)
                    if result.endswith(" 0"):
                        raise ValueError("用户不存在")
                    await connection.execute("UPDATE control_state SET version=version+1 WHERE singleton=TRUE")
                elif scope == "account":
                    result = await connection.execute("UPDATE broker_accounts SET trade_enabled=$1, updated_at=now() WHERE account_ref=$2::uuid", enabled, key)
                    if result.endswith(" 0"):
                        raise ValueError("账户不存在")
                    await connection.execute("UPDATE control_state SET version=version+1 WHERE singleton=TRUE")
                else:
                    raise ValueError("不支持的控制范围")
                await connection.execute(
                    "INSERT INTO audit_events(actor_user_id, action, account_ref, outcome, reason) VALUES($1,$2,$3::uuid,'OK',$4)",
                    actor, f"trade.{scope}.{'enable' if enabled else 'disable'}", key if scope == "account" else None, reason,
                )
        return await self.load_controls()

    async def register_device(self, user_id: str, label: str, public_key_pem: str) -> tuple[str, ControlSnapshot]:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                exists = await connection.fetchval("SELECT enabled FROM app_users WHERE user_id=$1", user_id)
                if exists is None or not exists:
                    raise ValueError("用户尚未由管理员启用")
                device_id = await connection.fetchval(
                    "INSERT INTO devices(user_id,label,public_key_pem,enabled) VALUES($1,$2,$3,FALSE) RETURNING device_id",
                    user_id, label, public_key_pem,
                )
                await connection.execute("UPDATE control_state SET version=version+1 WHERE singleton=TRUE")
                await connection.execute(
                    "INSERT INTO audit_events(actor_user_id,device_id,action,outcome) VALUES($1,$2,'device.register','OK')",
                    user_id, device_id,
                )
        return str(device_id), await self.load_controls()

    async def set_device_enabled(self, device_id: str, enabled: bool, actor: str) -> ControlSnapshot:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                result = await connection.execute("UPDATE devices SET enabled=$1 WHERE device_id=$2::uuid", enabled, device_id)
                if result.endswith(" 0"):
                    raise ValueError("设备不存在")
                await connection.execute("UPDATE control_state SET version=version+1 WHERE singleton=TRUE")
                await connection.execute(
                    "INSERT INTO audit_events(actor_user_id,device_id,action,outcome) VALUES($1,$2::uuid,$3,'OK')",
                    actor, device_id, "device.enable" if enabled else "device.disable",
                )
        return await self.load_controls()

    async def record_login(self, user_id: str, device_id: str) -> None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "UPDATE devices SET last_seen_at=now() WHERE device_id=$1::uuid AND user_id=$2",
                    device_id, user_id,
                )
                await connection.execute(
                    "INSERT INTO audit_events(actor_user_id,device_id,action,outcome) VALUES($1,$2::uuid,'login.accepted','OK')",
                    user_id, device_id,
                )

    async def record_rejection(self, device_id: str, path: str, reason: str) -> None:
        try:
            safe_device = str(uuid.UUID(device_id))
        except (ValueError, AttributeError):
            safe_device = None
        await self.pool.execute(
            "INSERT INTO audit_events(actor_user_id,device_id,action,outcome,reason) VALUES('unknown',$1::uuid,$2,'REJECTED',$3)",
            safe_device, f"request.reject:{path[:80]}", reason[:200],
        )

    async def upsert_user(self, user_id: str, casdoor_name: str, actor: str) -> ControlSnapshot:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO app_users(user_id,casdoor_name,enabled,trade_enabled) VALUES($1,$2,TRUE,FALSE) "
                    "ON CONFLICT(user_id) DO UPDATE SET casdoor_name=EXCLUDED.casdoor_name, enabled=TRUE, updated_at=now()", user_id, casdoor_name,
                )
                await connection.execute("UPDATE control_state SET version=version+1 WHERE singleton=TRUE")
                await connection.execute(
                    "INSERT INTO audit_events(actor_user_id,action,outcome,reason) VALUES($1,'user.create','OK',$2)",
                    actor, user_id,
                )
        return await self.load_controls()

    async def set_user_enabled(self, user_id: str, enabled: bool, actor: str) -> ControlSnapshot:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                result = await connection.execute(
                    "UPDATE app_users SET enabled=$1, updated_at=now() WHERE user_id=$2", enabled, user_id,
                )
                if result.endswith(" 0"):
                    raise ValueError("用户不存在")
                await connection.execute("UPDATE control_state SET version=version+1 WHERE singleton=TRUE")
                await connection.execute(
                    "INSERT INTO audit_events(actor_user_id,action,outcome,reason) VALUES($1,$2,'OK',$3)",
                    actor, "user.enable" if enabled else "user.disable", user_id,
                )
        return await self.load_controls()

    async def casdoor_name(self, user_id: str) -> str:
        name = await self.pool.fetchval("SELECT casdoor_name FROM app_users WHERE user_id=$1", user_id)
        if not name:
            raise ValueError("用户不存在或缺少身份系统映射")
        return str(name)

    async def add_broker_account(self, broker_account_id: str, alias: str, account_mask: str, actor: str) -> tuple[str, ControlSnapshot]:
        value = self.cipher.encrypt(broker_account_id)
        fingerprint = self.cipher.fingerprint(broker_account_id)
        try:
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    account_ref = await connection.fetchval(
                        "INSERT INTO broker_accounts(broker_account_cipher,account_fingerprint,account_mask,alias,enabled,trade_enabled) "
                        "VALUES($1,$2,$3,$4,TRUE,FALSE) RETURNING account_ref", value, fingerprint, account_mask, alias,
                    )
                    await connection.execute("UPDATE control_state SET version=version+1 WHERE singleton=TRUE")
                    await connection.execute(
                        "INSERT INTO audit_events(actor_user_id,action,account_ref,outcome) VALUES($1,'account.register',$2::uuid,'OK')",
                        actor, account_ref,
                    )
        except Exception as exc:
            existing = await self.pool.fetchval(
                "SELECT account_ref FROM broker_accounts WHERE account_fingerprint=$1", fingerprint,
            )
            if existing is not None:
                raise ValueError("该机构账户已登记") from None
            raise exc
        return str(account_ref), await self.load_controls()

    async def bind_account(self, user_id: str, account_ref: str, actor: str) -> ControlSnapshot:
        try:
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    await connection.execute(
                        "INSERT INTO user_account_bindings(user_id,account_ref) VALUES($1,$2::uuid)", user_id, account_ref,
                    )
                    await connection.execute("UPDATE control_state SET version=version+1 WHERE singleton=TRUE")
                    await connection.execute(
                        "INSERT INTO audit_events(actor_user_id,action,account_ref,outcome,reason) VALUES($1,'account.bind',$2::uuid,'OK',$3)",
                        actor, account_ref, user_id,
                    )
        except Exception as exc:
            raise ValueError("账户绑定失败；账户可能已分配给其他用户") from exc
        return await self.load_controls()

    async def unbind_account(self, account_ref: str, actor: str) -> ControlSnapshot:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                result = await connection.execute(
                    "DELETE FROM user_account_bindings WHERE account_ref=$1::uuid", account_ref,
                )
                if result.endswith(" 0"):
                    raise ValueError("账户尚未绑定")
                await connection.execute("UPDATE control_state SET version=version+1 WHERE singleton=TRUE")
                await connection.execute(
                    "INSERT INTO audit_events(actor_user_id,action,account_ref,outcome) VALUES($1,'account.unbind',$2::uuid,'OK')",
                    actor, account_ref,
                )
        return await self.load_controls()

    async def admin_snapshot(self) -> dict[str, Any]:
        async with self.pool.acquire() as connection:
            users = await connection.fetch("SELECT user_id,casdoor_name,enabled,trade_enabled,updated_at FROM app_users ORDER BY casdoor_name")
            accounts = await connection.fetch(
                "SELECT a.account_ref,a.account_mask,a.alias,a.enabled,a.trade_enabled,b.user_id "
                "FROM broker_accounts a LEFT JOIN user_account_bindings b USING(account_ref) ORDER BY a.alias"
            )
            devices = await connection.fetch(
                "SELECT device_id,user_id,label,enabled,created_at,last_seen_at FROM devices ORDER BY created_at DESC"
            )
            state = await connection.fetchrow("SELECT global_trade_enabled,version,reason,updated_at,updated_by FROM control_state WHERE singleton=TRUE")
        def clean(row: Any) -> dict[str, Any]:
            return {key: (value.isoformat() if isinstance(value, datetime) else str(value) if key.endswith("_ref") or key.endswith("_id") else value) for key, value in dict(row).items()}
        return {"control": clean(state), "users": [clean(row) for row in users], "accounts": [clean(row) for row in accounts], "devices": [clean(row) for row in devices]}

    async def registered_broker_account_ids(self) -> set[str]:
        rows = await self.pool.fetch("SELECT broker_account_cipher FROM broker_accounts")
        return {self.cipher.decrypt(row["broker_account_cipher"]) for row in rows}

    async def find_request(self, account_ref: str, request_id: str) -> tuple[ServerOrder, str] | None:
        row = await self.pool.fetchrow(
            "SELECT i.payload_hash, o.* FROM idempotency_keys i JOIN orders o USING(account_ref, client_order_id) "
            "WHERE i.account_ref=$1::uuid AND i.request_id=$2::uuid", account_ref, request_id,
        )
        return (self._row(row), str(row["payload_hash"])) if row else None

    async def find(self, account_ref: str, client_order_id: str) -> ServerOrder | None:
        row = await self.pool.fetchrow("SELECT * FROM orders WHERE account_ref=$1::uuid AND client_order_id=$2", account_ref, client_order_id)
        return self._row(row) if row else None

    async def insert(self, order: ServerOrder) -> ServerOrder:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                existing = await connection.fetchrow(
                    "SELECT payload_hash, client_order_id FROM idempotency_keys WHERE account_ref=$1::uuid AND request_id=$2::uuid FOR UPDATE",
                    order.account_ref, order.request_id,
                )
                if existing:
                    found = await connection.fetchrow("SELECT * FROM orders WHERE account_ref=$1::uuid AND client_order_id=$2", order.account_ref, existing["client_order_id"])
                    return self._row(found)
                await connection.execute(
                    "INSERT INTO orders(account_ref,client_order_id,origin_request_id,user_id,device_id,symbol,side,order_type,quantity,limit_price,reference_price,estimated_notional,status,version,message,created_at,updated_at) "
                    "VALUES($1::uuid,$2,$3::uuid,$4,$5::uuid,$6,$7,$8,$9,$10::numeric,$11::numeric,$12::numeric,$13,$14,$15,to_timestamp($16),to_timestamp($17))",
                    order.account_ref, order.client_order_id, order.request_id, order.user_id, order.device_id, order.symbol, order.side,
                    order.order_type, order.quantity, order.limit_price, order.reference_price, order.estimated_notional,
                    order.status, order.version, order.message, order.created_at, order.updated_at,
                )
                await connection.execute(
                    "INSERT INTO idempotency_keys(account_ref,request_id,payload_hash,client_order_id) VALUES($1::uuid,$2::uuid,$3,$4)",
                    order.account_ref, order.request_id, order.payload_hash, order.client_order_id,
                )
                await connection.execute(
                    "INSERT INTO order_events(account_ref,client_order_id,version,status,payload) VALUES($1::uuid,$2,$3,$4,$5::jsonb)",
                    order.account_ref, order.client_order_id, order.version, order.status,
                    json.dumps(order.public(), ensure_ascii=False),
                )
                await connection.execute(
                    "INSERT INTO audit_events(actor_user_id,device_id,action,account_ref,client_order_id,outcome) "
                    "VALUES($1,$2::uuid,'order.submit',$3::uuid,$4,'RECEIVED')",
                    order.user_id, order.device_id, order.account_ref, order.client_order_id,
                )
        return order

    async def remember_request(self, order: ServerOrder, request_id: str, payload_hash: str) -> None:
        try:
            await self.pool.execute(
                "INSERT INTO idempotency_keys(account_ref,request_id,payload_hash,client_order_id) VALUES($1::uuid,$2::uuid,$3,$4)",
                order.account_ref, request_id, payload_hash, order.client_order_id,
            )
        except Exception as exc:
            row = await self.find_request(order.account_ref, request_id)
            if row is None or row[0].client_order_id != order.client_order_id or row[1] != payload_hash:
                if row is not None:
                    raise ValueError("相同 Idempotency-Key 的操作字段不一致") from None
                raise exc

    async def update(self, order: ServerOrder, status: str, *, message: str = "", result: object = None) -> ServerOrder:
        encoded = json.dumps(result, ensure_ascii=False, default=str) if result is not None else None
        row = await self.pool.fetchrow(
            "UPDATE orders SET status=$3, version=version+1, message=CASE WHEN $4='' THEN message ELSE $4 END, "
            "result_json=COALESCE($5::jsonb,result_json), limit_price=$6::numeric, reference_price=$7::numeric, estimated_notional=$8::numeric, updated_at=now() "
            "WHERE account_ref=$1::uuid AND client_order_id=$2 RETURNING *",
            order.account_ref, order.client_order_id, status, message, encoded, order.limit_price,
            order.reference_price, order.estimated_notional,
        )
        updated = self._row(row)
        await self.pool.execute(
            "INSERT INTO order_events(account_ref,client_order_id,version,status,payload) VALUES($1::uuid,$2,$3,$4,$5::jsonb) ON CONFLICT DO NOTHING",
            updated.account_ref, updated.client_order_id, updated.version, updated.status,
            json.dumps(updated.public(), ensure_ascii=False),
        )
        return updated

    async def recent(self, user_id: str, account_ref: str | None, limit: int = 50, before: tuple[float, str] | None = None) -> list[ServerOrder]:
        cursor_time = before[0] if before else None
        cursor_id = before[1] if before else None
        if account_ref:
            rows = await self.pool.fetch(
                "SELECT * FROM orders WHERE user_id=$1 AND account_ref=$2::uuid "
                "AND ($3::double precision IS NULL OR (created_at,client_order_id)<(to_timestamp($3),$4)) "
                "ORDER BY created_at DESC,client_order_id DESC LIMIT $5", user_id, account_ref, cursor_time, cursor_id, max(1, min(limit, 200)),
            )
        else:
            rows = await self.pool.fetch(
                "SELECT * FROM orders WHERE user_id=$1 AND ($2::double precision IS NULL OR "
                "(created_at,client_order_id)<(to_timestamp($2),$3)) ORDER BY created_at DESC,client_order_id DESC LIMIT $4",
                user_id, cursor_time, cursor_id, max(1, min(limit, 200)),
            )
        return [self._row(row) for row in rows]

    async def open_count(self, account_ref: str) -> int:
        return int(await self.pool.fetchval("SELECT count(*) FROM orders WHERE account_ref=$1::uuid AND status=ANY($2::text[])", account_ref, list(ACTIVE_STATUSES)))

    async def daily_notional(self, account_ref: str, since: float) -> float:
        value = await self.pool.fetchval(
            "SELECT COALESCE(sum(estimated_notional),0) FROM orders WHERE account_ref=$1::uuid AND created_at>=to_timestamp($2) AND status<>'REJECTED'",
            account_ref, since,
        )
        return float(value or 0)

    async def unresolved(self) -> list[ServerOrder]:
        rows = await self.pool.fetch(
            "SELECT * FROM orders WHERE status=ANY($1::text[]) ORDER BY created_at", list(ACTIVE_STATUSES)
        )
        return [self._row(row) for row in rows]

    async def audit(self, action: str, order: ServerOrder, outcome: str, reason: str = "") -> None:
        await self.pool.execute(
            "INSERT INTO audit_events(actor_user_id,device_id,action,account_ref,client_order_id,outcome,reason) "
            "VALUES($1,$2::uuid,$3,$4::uuid,$5,$6,$7)",
            order.user_id, order.device_id, action, order.account_ref, order.client_order_id,
            outcome[:32], reason[:200],
        )

    @staticmethod
    def _row(row: Any) -> ServerOrder:
        def epoch(value: Any) -> float:
            return value.timestamp() if isinstance(value, datetime) else float(value or 0)
        return ServerOrder(
            account_ref=str(row["account_ref"]), user_id=str(row["user_id"]), device_id=str(row["device_id"]),
            request_id=str(row["origin_request_id"] or ""), client_order_id=str(row["client_order_id"]), symbol=str(row["symbol"]),
            side=str(row["side"]), order_type=str(row["order_type"]), quantity=int(row["quantity"]),
            limit_price=str(row["limit_price"]) if row["limit_price"] is not None else None,
            reference_price=str(row["reference_price"]), estimated_notional=str(row["estimated_notional"]),
            status=str(row["status"]), version=int(row["version"]), message=str(row["message"] or ""),
            result=row["result_json"], created_at=epoch(row["created_at"]), updated_at=epoch(row["updated_at"]),
        )

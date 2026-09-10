from __future__ import annotations

import re
from typing import Any

from fast_order.credentials import mask_account_id

from .casdoor import CasdoorAdminClient
from .controls import ControlPlane
from .postgres import PostgresStore


USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,64}$")


class AdminService:
    def __init__(self, store: PostgresStore, controls: ControlPlane, casdoor: CasdoorAdminClient, discovered_accounts: set[str]):
        self.store = store
        self.controls = controls
        self.casdoor = casdoor
        self.discovered_accounts = discovered_accounts

    async def snapshot(self) -> dict[str, Any]:
        return await self.store.admin_snapshot()

    async def create_user(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        if set(payload) != {"username", "displayName", "password"}:
            raise ValueError("用户字段无效")
        username = str(payload["username"]).strip()
        display = str(payload["displayName"]).strip()
        password = str(payload["password"])
        if not USERNAME.fullmatch(username) or not (1 <= len(display) <= 80):
            raise ValueError("用户名或显示名称无效")
        if len(password) < 12 or len(password) > 128:
            raise ValueError("固定密码长度必须为12至128位")
        user_id = await self.casdoor.create_user(username, display, password)
        snapshot = await self.store.upsert_user(user_id, username, actor)
        self.controls.replace(snapshot)
        return {"userId": user_id, "controlVersion": snapshot.version}

    async def set_user_enabled(self, user_id: str, enabled: bool, actor: str) -> dict[str, Any]:
        username = await self.store.casdoor_name(user_id)
        if enabled:
            await self.casdoor.set_forbidden(username, False)
            snapshot = await self.store.set_user_enabled(user_id, True, actor)
        else:
            # Revoke the in-process authorization snapshot before making the
            # slower IAM call. Existing JWTs stop working immediately.
            snapshot = await self.store.set_user_enabled(user_id, False, actor)
            self.controls.replace(snapshot)
            await self.casdoor.set_forbidden(username, True)
        self.controls.replace(snapshot)
        return {"ok": True, "controlVersion": snapshot.version}

    async def reset_password(self, user_id: str, password: str) -> dict[str, Any]:
        if len(password) < 12 or len(password) > 128:
            raise ValueError("固定密码长度必须为12至128位")
        username = await self.store.casdoor_name(user_id)
        await self.casdoor.set_password(username, password)
        return {"ok": True}

    async def add_account(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        if set(payload) != {"brokerAccountId", "alias"}:
            raise ValueError("账户字段无效")
        account_id = str(payload["brokerAccountId"]).strip()
        alias = str(payload["alias"]).strip()
        if account_id not in self.discovered_accounts:
            raise ValueError("账户不在机构凭据发现列表中")
        if not (1 <= len(alias) <= 80):
            raise ValueError("账户别名无效")
        account_ref, snapshot = await self.store.add_broker_account(account_id, alias, mask_account_id(account_id), actor)
        self.controls.replace(snapshot)
        return {"accountRef": account_ref, "controlVersion": snapshot.version}

    async def bind(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        if set(payload) != {"userId", "accountRef"}:
            raise ValueError("绑定字段无效")
        snapshot = await self.store.bind_account(str(payload["userId"]), str(payload["accountRef"]), actor)
        self.controls.replace(snapshot)
        return {"ok": True, "controlVersion": snapshot.version}

    async def unbind(self, account_ref: str, actor: str) -> dict[str, Any]:
        snapshot = await self.store.unbind_account(account_ref, actor)
        self.controls.replace(snapshot)
        return {"ok": True, "controlVersion": snapshot.version}

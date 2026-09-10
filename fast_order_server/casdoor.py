from __future__ import annotations

import json
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CasdoorAdminConfig:
    base_url: str
    client_id: str
    client_secret: str
    organization: str
    application: str


class CasdoorAdminClient:
    def __init__(self, config: CasdoorAdminConfig):
        self.config = config

    async def create_user(self, username: str, display_name: str, password: str) -> str:
        import asyncio
        return await asyncio.to_thread(self._create_user, username, display_name, password)

    def _create_user(self, username: str, display_name: str, password: str) -> str:
        token = self._client_token()
        payload = {
            "owner": self.config.organization,
            "name": username,
            "displayName": display_name,
            "password": password,
            "type": "normal-user",
            "signupApplication": self.config.application,
            "isForbidden": False,
        }
        result = self._request("/api/add-user", payload, token)
        status = str(result.get("status") or "ok").lower() if isinstance(result, dict) else "ok"
        if status not in {"ok", "success"}:
            raise RuntimeError("身份系统拒绝创建用户")
        data = self._get("/api/get-user", {"id": f"{self.config.organization}/{username}"}, token).get("data")
        if not isinstance(data, dict) or not data.get("id"):
            raise RuntimeError("身份系统创建用户后无法取得稳定用户ID")
        return str(data["id"])

    async def set_forbidden(self, username: str, forbidden: bool) -> None:
        import asyncio
        await asyncio.to_thread(self._set_forbidden, username, forbidden)

    def _set_forbidden(self, username: str, forbidden: bool) -> None:
        token = self._client_token()
        user = self._get("/api/get-user", {"id": f"{self.config.organization}/{username}"}, token).get("data")
        if not isinstance(user, dict):
            raise RuntimeError("身份系统用户不存在")
        user["isForbidden"] = forbidden
        result = self._request(f"/api/update-user?id={urllib.parse.quote(self.config.organization + '/' + username)}", user, token)
        self._ensure_ok(result, "身份系统拒绝修改用户状态")

    async def set_password(self, username: str, password: str) -> None:
        import asyncio
        await asyncio.to_thread(self._set_password, username, password)

    def _set_password(self, username: str, password: str) -> None:
        token = self._client_token()
        result = self._request_form("/api/set-password", {
            "userOwner": self.config.organization, "userName": username,
            "oldPassword": "", "newPassword": password,
        }, token)
        self._ensure_ok(result, "身份系统拒绝重置密码")

    def _client_token(self) -> str:
        result = self._request(
            "/api/login/oauth/access_token",
            {"grant_type": "client_credentials", "client_id": self.config.client_id, "client_secret": self.config.client_secret},
            None,
        )
        token = str(result.get("access_token") or "") if isinstance(result, dict) else ""
        if not token:
            raise RuntimeError("身份系统管理令牌不可用")
        return token

    def _request(self, path: str, payload: dict[str, Any], token: str | None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read(256 * 1024))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError("身份系统管理接口不可用") from exc

    def _request_form(self, path: str, payload: dict[str, str], token: str) -> dict[str, Any]:
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + path,
            data=urllib.parse.urlencode(payload).encode("ascii"),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "Authorization": f"Bearer {token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read(256 * 1024))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError("身份系统管理接口不可用") from exc

    def _get(self, path: str, query: dict[str, str], token: str) -> dict[str, Any]:
        url = self.config.base_url.rstrip("/") + path + "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, headers={"Accept": "application/json", "Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read(256 * 1024))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError("身份系统管理接口不可用") from exc

    @staticmethod
    def _ensure_ok(result: dict[str, Any], message: str) -> None:
        if str(result.get("status") or "ok").lower() not in {"ok", "success"}:
            raise RuntimeError(message)

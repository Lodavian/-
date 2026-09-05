from __future__ import annotations

import asyncio
import inspect
import json
import logging
import secrets
import shutil
import time
from collections import deque
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from fast_order.config import Settings
from fast_order.connection_errors import classify_connection_error
from fast_order.credentials import (
    CredentialStore,
    CredentialStoreError,
    ResolvedCredentials,
    mask_account_id,
    resolve_credentials,
)
from fast_order.service import TradingService
from fast_order.utils import select_account_id
from fast_order.webull_gateway import WebullGateway


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
ORDER_FIELDS = {"requestId", "symbol", "side", "orderType", "quantity", "limitPrice"}
MAX_WS_CONNECTIONS = 8
MAX_COMMANDS_PER_MINUTE = 120
MAX_MUTATING_COMMANDS_PER_MINUTE = 24
SESSION_PROTOCOL_PREFIX = "fast-order-session."
BINDING_SESSION_TTL = 120.0
ERROR_DETAIL_TTL = 300.0


@web.middleware
async def security_headers_middleware(request: web.Request, handler):
    """Keep the local trading surface a same-origin, non-embeddable app."""

    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = web.Response(
            status=exc.status,
            reason=exc.reason,
            headers=exc.headers,
            text=exc.text,
        )
    response.headers.update(
        {
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self' ws: wss:; img-src 'self' data:; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            ),
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
        }
    )
    return response


class SdkSecretFilter(logging.Filter):
    """Drop verbose SDK records because Webull embeds signed headers in them."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith(("webull", "grpc"))


class FastOrderApplication:
    def __init__(self, settings: Settings, *, static_root: Path | None = None):
        self.settings = settings
        self.static_root = Path(static_root or getattr(settings, "resource_root", ROOT) / "static").resolve()
        self.session_token = secrets.token_urlsafe(32)
        self.websockets: set[web.WebSocketResponse] = set()
        self.controller_ws: web.WebSocketResponse | None = None
        self.websocket_roles: dict[web.WebSocketResponse, str] = {}
        self.gateway = WebullGateway(settings, self._receive_gateway_event)
        self.service = TradingService(settings, self.gateway, self.broadcast, market_active=False)
        app_paths = getattr(settings, "app_paths", None)
        project_root = Path(getattr(settings, "project_root", ROOT))
        self.credential_store = CredentialStore(app_paths or project_root)
        self._resolved_credentials: ResolvedCredentials | None = None
        self._session_credentials: tuple[str, str, str] | None = None
        self._binding_sessions: dict[str, dict[str, Any]] = {}
        self._connection_errors: dict[str, dict[str, Any]] = {}
        try:
            self._resolved_credentials = resolve_credentials(
                self.credential_store,
                env_app_key=str(getattr(settings, "app_key", "")),
                env_app_secret=str(getattr(settings, "app_secret", "")),
                env_account_id=str(getattr(settings, "account_id", "")),
            )
            set_state = getattr(self.service, "set_credential_state", None)
            if self._resolved_credentials is None:
                if callable(set_state):
                    set_state("UNBOUND")
            elif self._resolved_credentials.source == "secure_store":
                if callable(set_state):
                    set_state("CREDENTIALS_SAVED")
            else:
                if callable(set_state):
                    set_state("ENV_FALLBACK")
        except CredentialStoreError:
            set_state = getattr(self.service, "set_credential_state", None)
            if callable(set_state):
                set_state("ERROR")
        self.websocket_visibility: dict[web.WebSocketResponse, bool] = {}
        self._send_locks: dict[web.WebSocketResponse, asyncio.Lock] = {}
        self._command_times: dict[web.WebSocketResponse, deque[float]] = {}
        self._mutating_command_times: dict[web.WebSocketResponse, deque[float]] = {}
        self._activity_lock = asyncio.Lock()
        self.app = web.Application(client_max_size=32 * 1024, middlewares=[security_headers_middleware])
        self.app.add_routes(
            [
                web.get("/", self.index),
                web.get("/styles.css", self.static_file),
                web.get("/app.js", self.static_file),
                web.get("/health", self.health),
                web.get("/livez", self.livez),
                web.get("/readyz", self.readyz),
                web.get("/ws", self.websocket),
            ]
        )
        self.app.on_startup.append(self.on_startup)
        self.app.on_cleanup.append(self.on_cleanup)
        self._status_task: asyncio.Task[None] | None = None

    async def index(self, _request: web.Request) -> web.Response:
        html = (self.static_root / "index.html").read_text(encoding="utf-8")
        html = html.replace("__SESSION_TOKEN__", self.session_token)
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})

    async def static_file(self, request: web.Request) -> web.FileResponse:
        name = request.path.lstrip("/")
        if name not in {"styles.css", "app.js"}:
            raise web.HTTPNotFound()
        return web.FileResponse(self.static_root / name, headers={"Cache-Control": "no-store"})

    async def health(self, _request: web.Request) -> web.Response:
        # Keep the original endpoint for existing local launchers/monitors.
        payload = await self._service_health("livez")
        payload.setdefault("ok", True)
        payload.setdefault("status", self.service.connection_status)
        payload.setdefault("symbol", self.service.current_symbol)
        payload.setdefault("sandbox", self.settings.sandbox)
        payload.setdefault("marketActive", self.service.market_active)
        return web.json_response(payload)

    async def livez(self, _request: web.Request) -> web.Response:
        payload = await self._service_health("livez")
        return web.json_response(payload, status=200 if payload.get("ok", False) else 503)

    async def readyz(self, _request: web.Request) -> web.Response:
        payload = await self._service_health("readyz")
        return web.json_response(payload, status=200 if payload.get("ok", False) else 503)

    async def _service_health(self, method_name: str) -> dict[str, Any]:
        method = getattr(self.service, method_name, None)
        if callable(method):
            try:
                value = await self._maybe_await(method())
            except Exception:
                logging.getLogger("fast_order").warning("Health check failed: %s", method_name)
                return {"ok": False, "status": "error"}
            if isinstance(value, dict):
                return dict(value)
            return {"ok": bool(value), "status": "ready" if value else "not_ready"}
        if method_name == "livez":
            return {"ok": True, "status": "alive"}
        return {
            "ok": self.service.connection_status in {"connected", "degraded"},
            "status": self.service.connection_status,
            "symbol": self.service.current_symbol,
            "sandbox": self.settings.sandbox,
            "marketActive": self.service.market_active,
        }

    async def websocket(self, request: web.Request) -> web.StreamResponse:
        if request.query.get("token"):
            raise web.HTTPUnauthorized(text="WebSocket token must use Sec-WebSocket-Protocol")
        expected_protocol = f"{SESSION_PROTOCOL_PREFIX}{self.session_token}"
        offered_protocols = {
            item.strip() for item in request.headers.get("Sec-WebSocket-Protocol", "").split(",") if item.strip()
        }
        if expected_protocol not in offered_protocols:
            raise web.HTTPUnauthorized(text="invalid session token")
        expected_origins = {
            f"http://127.0.0.1:{self.settings.port}",
            f"http://localhost:{self.settings.port}",
        }
        if request.headers.get("Origin") not in expected_origins:
            raise web.HTTPForbidden(text="local same-origin connections only")
        limit = int(getattr(self.settings, "max_ws_connections", MAX_WS_CONNECTIONS))
        if len(self.websockets) >= max(1, min(limit, MAX_WS_CONNECTIONS)):
            raise web.HTTPServiceUnavailable(text="本地连接数已达上限")
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=16 * 1024, protocols=(expected_protocol,))
        await ws.prepare(request)
        self.websockets.add(ws)
        self.websocket_roles[ws] = "controller" if self.controller_ws is None else "readonly"
        if self.websocket_roles[ws] == "controller":
            self.controller_ws = ws
        self.websocket_visibility[ws] = False
        self._send_locks[ws] = asyncio.Lock()
        self._command_times[ws] = deque()
        self._mutating_command_times[ws] = deque()
        initial = self.service.initial_state()
        if isinstance(initial, dict):
            initial = dict(initial)
            initial["account"] = self._account_state_event()
            connection = dict(initial.get("connection") or {})
            connection["controllerRole"] = self.websocket_roles[ws]
            connection["readOnly"] = self.websocket_roles[ws] != "controller"
            initial["connection"] = connection
        await self._send_json(ws, initial)
        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    await self.handle_command(ws, message.data)
                elif message.type == WSMsgType.ERROR:
                    logging.getLogger("fast_order").warning("WebSocket error: %s", type(ws.exception()).__name__)
        finally:
            self.websockets.discard(ws)
            self.websocket_visibility.pop(ws, None)
            self.websocket_roles.pop(ws, None)
            self._send_locks.pop(ws, None)
            self._command_times.pop(ws, None)
            self._mutating_command_times.pop(ws, None)
            if self.controller_ws is ws:
                self.controller_ws = next(iter(self.websockets), None)
                if self.controller_ws is not None:
                    self.websocket_roles[self.controller_ws] = "controller"
            await self._broadcast_role_states()
            await self._sync_market_activity()
        return ws

    async def handle_command(self, ws: web.WebSocketResponse, raw: str) -> None:
        try:
            if not self._allow_command(ws, raw):
                raise ValueError("操作过于频繁，请稍后重试")
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError("消息必须是 JSON 对象")
            kind = str(message.get("type") or "")
            payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
            role = self.websocket_roles.get(ws, "readonly")
            account_commands = {
                "account.binding.start", "account.binding.complete", "account.connect",
                "account.binding.cancel", "account.disconnect", "account.forget", "account.switch", "account.settings",
            }
            if kind in {"connect", "symbol.change", "order.submit", "order.cancel", "live.arm", "live.disarm", *account_commands} and role != "controller":
                raise PermissionError("当前标签页为只读，请使用控制标签页操作")
            if kind == "connect":
                try:
                    await self._connect_resolved_credentials()
                except Exception as exc:
                    error = self._remember_connection_error(exc)
                    await self._send_error(ws, error["message"], **{key: value for key, value in error.items() if key != "message"})
            elif kind == "account.binding.start":
                await self._handle_binding_start(ws, payload)
            elif kind == "account.binding.complete":
                await self._handle_binding_complete(ws, payload)
            elif kind == "account.binding.cancel":
                if set(payload) - {"bindingSessionId"}:
                    raise ValueError("取消绑定包含禁止字段")
                await self._cancel_binding(str(payload.get("bindingSessionId") or ""))
            elif kind == "account.connect":
                if set(payload):
                    raise ValueError("连接账户不接受参数")
                try:
                    await self._connect_resolved_credentials()
                except Exception as exc:
                    error = self._remember_connection_error(exc)
                    await self._send_error(ws, error["message"], **{key: value for key, value in error.items() if key != "message"})
            elif kind == "account.disconnect":
                if set(payload):
                    raise ValueError("断开账户不接受参数")
                await self.service.disconnect()
                await self.broadcast(self._account_state_event())
            elif kind == "account.switch":
                if set(payload):
                    raise ValueError("切换账户不接受参数")
                await self.service.disconnect("请选择新的券商API账户")
                await self.broadcast(self._account_state_event())
            elif kind == "account.settings":
                if set(payload):
                    raise ValueError("API 设置查询不接受参数")
                await self._send_json(ws, self._account_state_event())
            elif kind == "account.forget":
                if set(payload) - {"confirm"} or payload.get("confirm") is not True:
                    raise ValueError("忘记凭据必须明确确认")
                await self._forget_credentials()
            elif kind == "connection.error.details":
                if set(payload) - {"errorId"}:
                    raise ValueError("错误详情包含禁止字段")
                await self._send_json(ws, self._error_detail(str(payload.get("errorId") or "")))
            elif kind == "page.visibility":
                if set(payload) - {"visible"}:
                    raise ValueError("页面状态包含禁止字段")
                self.websocket_visibility[ws] = payload.get("visible") is True
                await self._sync_market_activity()
            elif kind == "symbol.change":
                await self.service.switch_symbol(payload.get("symbol"))
            elif kind == "symbol.search":
                if set(payload) - {"query"}:
                    raise ValueError("股票搜索包含禁止字段")
                await self._send_json(ws, await self.service.search_symbols(payload.get("query")))
            elif kind == "order.submit":
                extra = set(payload) - ORDER_FIELDS
                if extra:
                    raise ValueError(f"订单包含禁止字段: {', '.join(sorted(extra))}")
                await self.service.submit_order(payload)
            elif kind == "order.cancel":
                if set(payload) - {"clientOrderId"}:
                    raise ValueError("撤单请求包含禁止字段")
                await self.service.cancel_order(payload.get("clientOrderId"))
            elif kind == "live.arm":
                if set(payload):
                    raise ValueError("交易开关开启不接受参数")
                arm = getattr(self.service, "arm_live", None)
                if not callable(arm):
                    raise RuntimeError("当前服务不支持交易开关")
                await self._maybe_await(arm())
                risk_method = getattr(self.service, "risk_event", None)
                event = risk_method() if callable(risk_method) else self.service.connection_event()
                await self._send_json(ws, await self._maybe_await(event))
            elif kind == "live.disarm":
                if set(payload):
                    raise ValueError("交易开关关闭不接受参数")
                disarm = getattr(self.service, "disarm_live", None)
                if not callable(disarm):
                    raise RuntimeError("当前服务不支持交易开关")
                await self._maybe_await(disarm("交易开关已关闭"))
                risk_method = getattr(self.service, "risk_event", None)
                event = risk_method() if callable(risk_method) else self.service.connection_event()
                await self._send_json(ws, await self._maybe_await(event))
            else:
                raise ValueError(f"不支持的命令: {kind}")
        except json.JSONDecodeError:
            await self._send_error(ws, "消息格式无效")
        except PermissionError as exc:
            await self._send_error(ws, str(exc))
        except Exception as exc:
            logging.getLogger("fast_order").warning("Command rejected: %s", type(exc).__name__)
            await self._send_error(ws, self._safe_error_message(exc))

    def _allow_command(self, ws: web.WebSocketResponse, raw: str) -> bool:
        now = time.monotonic()
        history = self._command_times.setdefault(ws, deque())
        while history and now - history[0] > 60:
            history.popleft()
        if len(raw) > 16 * 1024 or len(history) >= MAX_COMMANDS_PER_MINUTE:
            return False
        history.append(now)
        try:
            kind = str((json.loads(raw) or {}).get("type") or "")
        except (TypeError, json.JSONDecodeError):
            kind = ""
        if kind in {
            "connect", "symbol.change", "order.submit", "order.cancel", "live.arm", "live.disarm",
            "account.binding.start", "account.binding.complete", "account.connect", "account.disconnect",
            "account.binding.cancel", "account.forget", "account.switch", "account.settings",
        }:
            mutations = self._mutating_command_times.setdefault(ws, deque())
            while mutations and now - mutations[0] > 60:
                mutations.popleft()
            if len(mutations) >= MAX_MUTATING_COMMANDS_PER_MINUTE:
                return False
            mutations.append(now)
        return True

    @staticmethod
    def _safe_error_message(exc: Exception) -> str:
        if isinstance(exc, (ValueError, RuntimeError, PermissionError)):
            message = str(exc).strip()
            sensitive = ("token", "secret", "app_key", "access_key", "authorization", "signature")
            if message and len(message) <= 240 and not any(item in message.lower() for item in sensitive):
                return message
        return "操作失败，请稍后重试；如订单状态不确定，请按 client order ID 核对"

    async def _send_error(self, ws: web.WebSocketResponse, message: str, **fields: Any) -> None:
        if not ws.closed:
            await self._send_json(ws, {"type": "error", "message": message, **fields})

    def _account_state_event(self) -> dict[str, Any]:
        profile = None
        try:
            profile = self.credential_store.load_profile()
        except CredentialStoreError:
            pass
        return {
            "type": "account.state",
            "credentialState": getattr(self.service, "credential_state", "UNBOUND"),
            "connectionState": self.service.connection_event().get("connectionState"),
            "accountMask": getattr(self.service, "account_mask", "") or (profile.account_mask if profile else ""),
            "autoConnect": bool(profile and profile.auto_connect),
            "environment": "SANDBOX" if self.settings.sandbox else "LIVE",
            "canRemember": self.credential_store.available,
        }

    @staticmethod
    def _binding_value(payload: dict[str, Any], name: str, maximum: int) -> str:
        value = payload.get(name)
        if not isinstance(value, str):
            raise ValueError(f"{name} 格式无效")
        value = value.strip()
        if not value or len(value) > maximum:
            raise ValueError(f"{name} 格式无效")
        return value

    def _purge_ephemeral_state(self) -> None:
        now = time.monotonic()
        self._binding_sessions = {
            key: item for key, item in self._binding_sessions.items() if item["expiresAt"] > now
        }
        self._connection_errors = {
            key: item for key, item in self._connection_errors.items() if item["expiresAt"] > now
        }

    async def _handle_binding_start(self, ws: web.WebSocketResponse, payload: dict[str, Any]) -> None:
        allowed = {"appKey", "appSecret", "rememberCredentials", "autoConnect"}
        if set(payload) - allowed:
            raise ValueError("账户绑定包含禁止字段")
        app_key = self._binding_value(payload, "appKey", 256)
        app_secret = self._binding_value(payload, "appSecret", 512)
        remember = payload.get("rememberCredentials") is True
        auto_connect = payload.get("autoConnect") is True
        if auto_connect and not remember:
            raise ValueError("自动连接必须同时选择安全保存凭据")
        try:
            accounts = await self.service.begin_account_binding(app_key, app_secret)
            session_id = secrets.token_urlsafe(18)
            refs: dict[str, str] = {}
            choices: list[dict[str, str]] = []
            for row in accounts:
                account_id = select_account_id([row], "", "")
                account_ref = secrets.token_urlsafe(16)
                refs[account_ref] = account_id
                choices.append({
                    "accountRef": account_ref,
                    "accountMask": mask_account_id(account_id),
                    "accountType": str(row.get("account_type") or row.get("accountType") or "交易账户")[:40],
                })
            self._purge_ephemeral_state()
            self._binding_sessions.clear()
            self._binding_sessions[session_id] = {
                "expiresAt": time.monotonic() + BINDING_SESSION_TTL,
                "appKey": app_key,
                "appSecret": app_secret,
                "remember": remember,
                "autoConnect": auto_connect,
                "accounts": refs,
            }
            if len(choices) == 1:
                warning = await self._complete_binding(session_id, choices[0]["accountRef"])
                await self._send_json(ws, {"type": "account.binding.complete", "autoSelected": True, "warning": warning})
            else:
                await self._send_json(ws, {
                    "type": "account.binding.accounts",
                    "bindingSessionId": session_id,
                    "expiresIn": int(BINDING_SESSION_TTL),
                    "accounts": choices,
                })
        except Exception as exc:
            error = self._remember_connection_error(exc, secrets_to_redact=(app_key, app_secret))
            try:
                await self.gateway.close()
                self.gateway.clear_credentials()
            except Exception:
                pass
            await self._send_error(ws, error["message"], **{key: value for key, value in error.items() if key != "message"})

    async def _handle_binding_complete(self, ws: web.WebSocketResponse, payload: dict[str, Any]) -> None:
        if set(payload) - {"bindingSessionId", "accountRef"}:
            raise ValueError("账户选择包含禁止字段")
        try:
            warning = await self._complete_binding(
                str(payload.get("bindingSessionId") or ""),
                str(payload.get("accountRef") or ""),
            )
            await self._send_json(ws, {"type": "account.binding.complete", "autoSelected": False, "warning": warning})
        except Exception as exc:
            error = self._remember_connection_error(exc)
            await self._send_error(ws, error["message"], **{key: value for key, value in error.items() if key != "message"})

    async def _complete_binding(self, session_id: str, account_ref: str) -> str:
        self._purge_ephemeral_state()
        session = self._binding_sessions.pop(session_id, None)
        if session is None:
            raise ValueError("账户绑定会话已失效，请重新验证")
        account_id = session["accounts"].get(account_ref)
        if not account_id:
            raise ValueError("账户选择无效，请重新验证")
        await self.service.complete_account_binding(account_id)
        app_key = session["appKey"]
        app_secret = session["appSecret"]
        warning = ""
        if session["remember"]:
            try:
                profile = self.credential_store.save_credentials(
                    app_key=app_key,
                    app_secret=app_secret,
                    account_id=account_id,
                    sandbox=self.settings.sandbox,
                    auto_connect=session["autoConnect"],
                )
                self._resolved_credentials = ResolvedCredentials(
                    app_key=app_key,
                    app_secret=app_secret,
                    account_id=account_id,
                    source="secure_store",
                    profile=profile,
                )
                self._session_credentials = None
                self.service.set_credential_state("CREDENTIALS_SAVED")
            except CredentialStoreError:
                self._session_credentials = (app_key, app_secret, account_id)
                self.service.set_credential_state("SESSION_ONLY")
                warning = "账户已连接，但 Windows Credential Manager 不可用；凭据仅本次运行有效"
        else:
            self._session_credentials = (app_key, app_secret, account_id)
            self.service.set_credential_state("SESSION_ONLY")
        await self.broadcast(self._account_state_event())
        await self.broadcast(self.service.connection_event())
        return warning

    async def _cancel_binding(self, session_id: str) -> None:
        session = self._binding_sessions.pop(session_id, None)
        if session is None:
            return
        if not self.gateway.connected and not self._binding_sessions:
            await self.gateway.close()
            self.gateway.clear_credentials()
            self.service.connection_status = "offline"
            self.service.connection_message = "账户绑定已取消"
            await self.broadcast(self.service.connection_event())

    async def _connect_resolved_credentials(self) -> None:
        if self.gateway.connected:
            return
        resolved = self._resolved_credentials
        if self._session_credentials is not None:
            app_key, app_secret, account_id = self._session_credentials
        elif resolved is not None:
            app_key, app_secret, account_id = resolved.app_key, resolved.app_secret, resolved.account_id
            if resolved.profile is not None:
                expected = "SANDBOX" if self.settings.sandbox else "LIVE"
                if resolved.profile.environment != expected:
                    raise ValueError("保存的账户环境与当前 Sandbox/LIVE 配置不一致")
        else:
            raise ValueError("尚未绑定券商API账户")
        try:
            accounts = await self.service.begin_account_binding(app_key, app_secret)
            selected = select_account_id(
                accounts,
                account_id,
                self.settings.account_hint if resolved and resolved.source == "env" else "",
            )
            await self.service.complete_account_binding(selected)
        except Exception:
            await self.gateway.close()
            self.gateway.clear_credentials()
            raise
        await self.broadcast(self._account_state_event())

    async def _forget_credentials(self) -> None:
        # Fail closed before awaiting any broker or filesystem operation.
        self.service.account_transitioning = True
        await self.broadcast(self.service.connection_event())
        await self.service.disconnect("正在清除账户绑定")
        self._binding_sessions.clear()
        self._session_credentials = None
        self._resolved_credentials = None
        self.gateway.clear_credentials()
        self.credential_store.delete_credentials()
        shutil.rmtree(self.settings.token_dir, ignore_errors=True)
        self.service.set_credential_state("UNBOUND")
        self.service.connection_status = "offline"
        self.service.connection_message = "尚未绑定券商API账户"
        await self.broadcast(self._account_state_event())
        await self.broadcast(self.service.connection_event())

    def _remember_connection_error(self, exc: BaseException, *, secrets_to_redact: tuple[str, ...] = ()) -> dict[str, Any]:
        self._purge_ephemeral_state()
        account_id = str(getattr(self.gateway, "account_id", "") or "")
        known = tuple(item for item in (*secrets_to_redact, account_id) if item)
        safe = classify_connection_error(exc, secrets=known)
        error_id = secrets.token_urlsafe(12)
        self._connection_errors[error_id] = {
            "expiresAt": time.monotonic() + ERROR_DETAIL_TTL,
            "detail": safe.detail,
        }
        return {
            "code": safe.code,
            "message": safe.summary,
            "retryable": safe.retryable,
            "source": "connection",
            "errorId": error_id,
        }

    def _error_detail(self, error_id: str) -> dict[str, Any]:
        self._purge_ephemeral_state()
        item = self._connection_errors.get(error_id)
        if item is None:
            raise ValueError("错误详情已失效")
        return {"type": "connection.error.detail", "errorId": error_id, "detail": item["detail"]}

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        return await value if inspect.isawaitable(value) else value

    async def _send_json(self, ws: web.WebSocketResponse, payload: dict[str, Any]) -> bool:
        if ws.closed:
            return False
        lock = self._send_locks.setdefault(ws, asyncio.Lock())
        try:
            async with lock:
                await asyncio.wait_for(ws.send_json(self._sanitize_outbound(payload)), timeout=1.0)
            return True
        except (asyncio.TimeoutError, ConnectionError, RuntimeError):
            return False

    @classmethod
    def _sanitize_outbound(cls, value: Any) -> Any:
        """Never expose a full account identifier through the browser channel."""

        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                normalized = str(key).replace("-", "_").lower()
                if normalized in {
                    "appsecret", "app_secret", "appkey", "app_key", "token", "access_token",
                    "authorization", "signature",
                }:
                    sanitized[key] = "***"
                elif normalized in {"accountid", "account_id", "accountnumber", "account_number", "accountno", "account_no"}:
                    sanitized[key] = cls._mask_account(item)
                elif normalized == "armphrasehint" and item:
                    hint = str(item)
                    sanitized[key] = f"LIVE {cls._mask_account(hint[5:].strip())}" if hint.upper().startswith("LIVE ") else "***"
                else:
                    sanitized[key] = cls._sanitize_outbound(item)
            return sanitized
        if isinstance(value, list):
            return [cls._sanitize_outbound(item) for item in value]
        return value

    @staticmethod
    def _mask_account(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        # Gateway values are already intentionally masked (for example
        # ``abc***xyz``). Preserve that exact value so the displayed LIVE
        # unlock phrase is the same phrase the service validates.
        if "•" in raw or "*" in raw:
            return raw
        return f"••••{raw[-4:]}"

    async def broadcast(self, event: dict[str, Any]) -> None:
        targets = tuple(self.websockets)
        results = await asyncio.gather(*(self._send_json(ws, event) for ws in targets))
        for ws, sent in zip(targets, results):
            if not sent:
                self.websockets.discard(ws)

    async def _broadcast_role_states(self) -> None:
        for ws in tuple(self.websockets):
            role = self.websocket_roles.get(ws, "readonly")
            await self._send_json(
                ws,
                {**self.service.connection_event(), "controllerRole": role, "readOnly": role != "controller"},
            )

    async def on_startup(self, _app: web.Application) -> None:
        self._status_task = asyncio.create_task(self._publish_status(), name="fast-order-status")
        profile = self._resolved_credentials.profile if self._resolved_credentials else None
        if profile is not None and profile.auto_connect:
            asyncio.create_task(self._auto_connect(), name="fast-order-auto-connect")

    async def _auto_connect(self) -> None:
        try:
            await self._connect_resolved_credentials()
        except Exception as exc:
            self._remember_connection_error(exc)
            logging.getLogger("fast_order").warning("Automatic account connection failed: %s", type(exc).__name__)

    async def on_cleanup(self, _app: web.Application) -> None:
        if self._status_task is not None:
            self._status_task.cancel()
            try:
                await self._status_task
            except asyncio.CancelledError:
                pass
        await self.service.close()

    async def _publish_status(self) -> None:
        while True:
            await asyncio.sleep(1)
            await self._expire_binding_sessions()
            await self.service.maintain_market_freshness()
            await self.broadcast(self.service.connection_event())

    async def _expire_binding_sessions(self) -> None:
        now = time.monotonic()
        expired = [key for key, item in self._binding_sessions.items() if item["expiresAt"] <= now]
        for key in expired:
            self._binding_sessions.pop(key, None)
        if expired and not self._binding_sessions and not self.gateway.connected:
            await self.gateway.close()
            self.gateway.clear_credentials()
            self.service.connection_status = "offline"
            self.service.connection_message = "账户绑定已超时，请重新验证"

    async def _sync_market_activity(self) -> None:
        async with self._activity_lock:
            # Any visible local tab benefits from live prices; only the controller
            # tab can mutate state or trade. When every tab is hidden, pause the
            # subscription and relock LIVE to save resources safely.
            active = any(
                self.websocket_visibility.get(ws, False)
                for ws in self.websockets
                if not ws.closed
            )
            await self.service.set_market_active(active)

    def _receive_gateway_event(self, event: dict[str, Any]) -> None:
        self.service.receive_gateway_event(event)


def main() -> None:
    # Keep app.py as the development entry point. The packaged executable uses
    # bootstrap.py, but both share the same server lifecycle implementation.
    from fast_order.bootstrap import development_main

    development_main(ROOT)


if __name__ == "__main__":
    main()

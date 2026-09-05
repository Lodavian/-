from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .config import Settings
from .credentials import harden_windows_acl
from .errors import BrokerRejectedError, GatewayUnavailableError, OrderUncertainError
from .utils import (
    available_position,
    normalize_symbol,
    protobuf_to_dict,
    response_json,
    response_ok,
    rows_from,
    safe_json,
    redact_text,
    search_instruments,
    select_account_id,
)


EventSink = Callable[[dict[str, Any]], None]


class WebullGateway:
    """Small one-account, one-symbol wrapper around Webull OpenAPI SDK 2.0.18."""

    SUB_TYPES = ["QUOTE", "SNAPSHOT", "TICK"]
    CATEGORY = "US_STOCK"

    def __init__(self, settings: Settings, event_sink: EventSink):
        self.settings = settings
        self.event_sink = event_sink
        self.account_id = ""
        self.trade_client: Any = None
        self.data_client: Any = None
        self.streaming_client: Any = None
        self.current_symbol = normalize_symbol(settings.default_symbol)
        self.connected = False
        self.stream_connected = False
        self.subscription_ready = False
        self.events_healthy = False
        self.stream_state = "DISCONNECTED"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream_ready: asyncio.Event | None = None
        self._subscribe_ready: asyncio.Event | None = None
        self._closing = False
        self._events_client: Any = None
        self._events_thread: threading.Thread | None = None
        self._events_ready: asyncio.Event | None = None
        self._market_paused = False
        self._subscription_lock = asyncio.Lock()
        self._stream_generation = 0
        self._stream_rebuild_task: asyncio.Task[Any] | None = None
        self._stream_rebuild_attempts = 0
        self._max_rebuild_attempts = 5
        self._logger = logging.getLogger("fast_order.webull")
        # Credentials are mutable only while the single gateway lifecycle is
        # fully disconnected.  This keeps UI binding compatible with the
        # existing .env fallback without creating a second TradeClient.
        self._app_key = settings.app_key
        self._app_secret = settings.app_secret
        self._configured_account_id = settings.account_id
        self._account_hint = settings.account_hint
        self._discovered_account_ids: list[str] = []

    @property
    def events_connected(self) -> bool:
        """Compatibility/readiness alias consumed by TradingService."""
        return self.events_healthy

    def _timeout(self, name: str, default: float) -> float:
        value = getattr(self.settings, name, default)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    async def _sdk_call(self, func: Callable[..., Any], *args: Any, timeout_name: str = "sdk_total_timeout", default_timeout: float = 30.0, **kwargs: Any) -> Any:
        """Run an SDK call off-loop with a hard deadline.

        ``to_thread`` cannot cancel a running SDK request, but the deadline
        prevents an unbounded coroutine and lets callers enter a safe state.
        The worker is deliberately not retried here: in particular, a place
        request may have reached the broker even if its response was lost.
        """
        timeout = self._timeout(timeout_name, default_timeout)
        try:
            return await asyncio.wait_for(asyncio.to_thread(func, *args, **kwargs), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"券商API请求超时 ({timeout:.1f}s)") from exc

    async def connect(self, market_active: bool = True) -> dict[str, Any]:
        if self.connected and self.stream_connected:
            return {"accountId": self._masked_account_id(), "symbol": self.current_symbol}
        accounts = await self.discover_accounts(self._app_key, self._app_secret)
        self.account_id = select_account_id(accounts, self._configured_account_id, self._account_hint)
        return await self.connect_selected(self.account_id, market_active=market_active)

    @property
    def has_credentials(self) -> bool:
        return bool(self._app_key and self._app_secret)

    def clear_credentials(self) -> None:
        if self.connected or self.streaming_client is not None or self._events_client is not None:
            raise RuntimeError("必须先断开券商API才能清理凭据")
        self._app_key = ""
        self._app_secret = ""
        self._configured_account_id = ""
        self._account_hint = ""
        self._discovered_account_ids.clear()

    async def discover_accounts(self, app_key: str, app_secret: str) -> list[dict[str, Any]]:
        """Validate credentials and return account rows inside the server only."""
        if self.connected or self.streaming_client is not None or self._events_client is not None:
            raise RuntimeError("已连接时不能替换 API 凭据，请先断开账户")
        key = str(app_key or "").strip()
        secret = str(app_secret or "").strip()
        if not key or not secret:
            raise ValueError("请输入 App Key 和 App Secret")
        self._app_key = key
        self._app_secret = secret
        self._configured_account_id = ""
        self._account_hint = ""
        self._prepare_connection_state()
        self.settings.token_dir.mkdir(parents=True, exist_ok=True)
        if not harden_windows_acl(self.settings.token_dir):
            raise RuntimeError("券商API令牌目录权限保护失败")
        await self._sdk_call(self._build_trade_clients, timeout_name="sdk_total_timeout", default_timeout=30.0)
        accounts_response = await self._sdk_call(self.trade_client.account_v2.get_account_list)
        if not response_ok(accounts_response):
            raise RuntimeError("券商API未能验证凭据或读取账户")
        accounts = rows_from(accounts_response)
        if not accounts:
            raise RuntimeError("该 OpenAPI 应用下没有可用交易账户")
        self._discovered_account_ids = [select_account_id([row], "", "") for row in accounts]
        return accounts

    async def connect_selected(self, account_id: str, market_active: bool = True) -> dict[str, Any]:
        """Finish the one gateway lifecycle with a server-side selected ID."""
        if self.connected:
            raise RuntimeError("券商API已连接，不能热切换账户")
        selected = str(account_id or "").strip()
        if not selected or selected not in self._discovered_account_ids:
            raise ValueError("账户选择已失效，请重新验证 API 凭据")
        if self.trade_client is None or self.data_client is None:
            raise RuntimeError("账户绑定会话已失效，请重新验证 API 凭据")
        self.account_id = selected
        self._configured_account_id = selected
        self._start_order_events()
        self._market_paused = not market_active
        try:
            await self._connect_market_stream_with_retry(market_active)
        except TimeoutError as exc:
            await self.close()
            raise RuntimeError("券商API实时行情连接或订阅超时") from exc
        try:
            await asyncio.wait_for(self._events_ready.wait(), timeout=self._timeout("sdk_connect_timeout", 10.0))
        except TimeoutError:
            # Keep the useful account/market lifecycle alive, but fail closed
            # for orders until gRPC explicitly confirms readiness.
            self.events_healthy = False
            self._emit({
                "type": "connection",
                "status": "degraded",
                "message": "账户与行情已连接，订单事件通道仍在连接；新订单已禁用",
            })
        self.connected = True
        self.stream_state = "PAUSED" if self._market_paused else "LIVE"
        return {
            "accountId": self._masked_account_id(),
            "symbol": self.current_symbol,
            "eventsConnected": self.events_healthy,
        }

    async def _connect_market_stream_with_retry(self, market_active: bool) -> None:
        """Retry only initial market-channel setup; never retries an order."""
        last_error: BaseException | None = None
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(float(attempt))
            self._stream_ready = asyncio.Event()
            self._subscribe_ready = asyncio.Event()
            self.stream_connected = False
            self.subscription_ready = False
            try:
                await self._sdk_call(self._build_streaming_client)
                client = self.streaming_client
                await self._sdk_call(
                    client.connect_and_loop_start,
                    1,
                    False,
                    None,
                    timeout_name="sdk_connect_timeout",
                    default_timeout=15.0,
                )
                await asyncio.wait_for(
                    self._stream_ready.wait(),
                    timeout=self._timeout("sdk_connect_timeout", 15.0),
                )
                if market_active:
                    await asyncio.wait_for(
                        self._subscribe_ready.wait(),
                        timeout=self._timeout("sdk_connect_timeout", 15.0),
                    )
                return
            except (TimeoutError, asyncio.TimeoutError, OSError) as exc:
                last_error = exc
                client, self.streaming_client = self.streaming_client, None
                if client is not None:
                    await self._shutdown_stream_client(client)
        raise TimeoutError("券商API实时行情连接重试已耗尽") from last_error

    def _prepare_connection_state(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stream_ready = asyncio.Event()
        self._subscribe_ready = asyncio.Event()
        self._events_ready = asyncio.Event()
        self._closing = False

    def _build_trade_clients(self) -> None:
        from webull.core.client import ApiClient
        from webull.data.data_client import DataClient
        from webull.trade.trade_client import TradeClient

        self._silence_sdk_loggers()

        api_client = ApiClient(self._app_key, self._app_secret, "hk")
        if self.settings.sandbox:
            api_client.add_endpoint("hk", self.settings.api_host)
        api_client._stream_logger_set = True
        api_client._file_logger_set = True
        api_client.set_token_dir(str(self.settings.token_dir.resolve()))
        self.trade_client = TradeClient(api_client)
        self.data_client = DataClient(api_client)

    def _build_streaming_client(self) -> None:
        from webull.data.data_streaming_client import DataStreamingClient

        self._silence_sdk_loggers()

        session_id = f"fast-{uuid.uuid4().hex[:16]}"
        generation = self._stream_generation + 1
        self._stream_generation = generation
        client = DataStreamingClient(
            self._app_key,
            self._app_secret,
            "hk",
            session_id,
            http_host=self.settings.api_host,
            mqtt_host=self.settings.data_host,
        )
        api_client = getattr(client, "api_client", None)
        if api_client is not None:
            api_client._stream_logger_set = True
            api_client._file_logger_set = True
            api_client.set_token_dir(str(self.settings.token_dir.resolve()))

        def on_connect(*_args: Any) -> None:
            if client is not self.streaming_client or generation != self._stream_generation or self._closing:
                return
            self.stream_connected = True
            self.stream_state = "CONNECTED"
            self._signal_event(self._stream_ready)
            if self._market_paused:
                self._emit({"type": "connection", "status": "connected", "message": "页面在后台，行情订阅已暂停"})
                return
            self._emit({"type": "connection", "status": "subscribing", "message": "实时行情已连接，正在订阅"})
            self._schedule_coroutine(self._subscribe_stream_client(client, generation))

        def on_disconnect(*_args: Any) -> None:
            if client is not self.streaming_client or generation != self._stream_generation:
                return
            self.stream_connected = False
            self.subscription_ready = False
            self.stream_state = "DEGRADED"
            if not self._closing:
                self._emit({"type": "connection", "status": "degraded", "message": "实时行情断线，SDK 正在重连"})
                if any(self._is_invalid_session(value) for value in _args):
                    self._request_stream_rebuild("INVALID_SESSION")

        def on_subscribe(*_args: Any) -> None:
            if client is not self.streaming_client or generation != self._stream_generation:
                return
            self.subscription_ready = True
            self.stream_state = "LIVE"
            self._signal_event(self._subscribe_ready)
            self._emit({"type": "connection", "status": "connected", "message": f"已订阅 {self.current_symbol}"})

        def on_connect_fail(*args: Any) -> None:
            if client is not self.streaming_client or generation != self._stream_generation:
                return
            self.stream_connected = False
            self.subscription_ready = False
            self.stream_state = "DEGRADED"
            reason = " ".join(str(arg) for arg in args)
            self._emit_error(f"实时行情连接失败: {self._safe_error(RuntimeError(reason or 'unknown'))}")
            if self._is_invalid_session(reason):
                self._request_stream_rebuild("INVALID_SESSION")

        def on_message(_client: Any, topic: Any, payload: Any) -> None:
            self._handle_market_message(str(topic), payload, generation)

        client.on_connect_success = on_connect
        client.on_disconnect = on_disconnect
        client.on_subscribe_success = on_subscribe
        client.on_connect_fail = on_connect_fail
        client.on_quotes_message = on_message
        self.streaming_client = client

    async def _subscribe_stream_client(self, client: Any, generation: int) -> None:
        if self._closing or client is not self.streaming_client or generation != self._stream_generation:
            return
        self.subscription_ready = False
        # Keep the event created by the connection attempt. Replacing it here
        # races with _connect_market_stream_with_retry(): the outer coroutine
        # may already be waiting on the original event while callbacks signal
        # the replacement, producing a false timeout after valid quotes arrive.
        if self._subscribe_ready is None:
            self._subscribe_ready = asyncio.Event()
        try:
            await self._sdk_call(
                client.subscribe,
                [self.current_symbol],
                self.CATEGORY,
                self.SUB_TYPES,
                1,
                False,
                timeout_name="sdk_total_timeout",
                default_timeout=15.0,
            )
            await asyncio.wait_for(self._subscribe_ready.wait(), timeout=self._timeout("sdk_connect_timeout", 15.0))
        except Exception as exc:
            self.stream_connected = False
            self.subscription_ready = False
            self.stream_state = "DEGRADED"
            self._emit_error(f"恢复行情订阅失败: {self._safe_error(exc)}")
            if self._is_invalid_session(exc):
                self._request_stream_rebuild("INVALID_SESSION")

    def _schedule_coroutine(self, coroutine: Any) -> None:
        if self._loop is None or self._closing:
            try:
                coroutine.close()
            except Exception:
                pass
            return
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        # Consume callback exceptions; the coroutine itself emits a safe error.
        future.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)

    @staticmethod
    def _is_invalid_session(value: Any) -> bool:
        text = str(value).upper()
        return "INVALID_SESSION" in text or "INVALID SESSION" in text or "SESSION_INVALID" in text

    @staticmethod
    def _safe_error(exc: BaseException) -> str:
        return redact_text(exc) or exc.__class__.__name__

    def _request_stream_rebuild(self, reason: str) -> None:
        if self._closing or self._loop is None:
            return
        if self._stream_rebuild_task is not None and not self._stream_rebuild_task.done():
            return
        self._stream_rebuild_task = asyncio.run_coroutine_threadsafe(
            self._rebuild_stream(reason), self._loop
        )

    async def _rebuild_stream(self, reason: str) -> None:
        self.stream_state = "REBUILDING"
        self.stream_connected = False
        self.subscription_ready = False
        self._emit({"type": "connection", "status": "degraded", "message": f"行情会话失效，正在重建（{reason}）"})
        for attempt in range(1, self._max_rebuild_attempts + 1):
            if self._closing:
                return
            self._stream_rebuild_attempts = attempt
            if attempt > 1:
                await asyncio.sleep(min(2 ** (attempt - 2), 8))
            old = self.streaming_client
            self.streaming_client = None
            self._stream_generation += 1
            await self._shutdown_stream_client(old)
            try:
                self._stream_ready = asyncio.Event()
                self._subscribe_ready = asyncio.Event()
                await self._sdk_call(self._build_streaming_client)
                client = self.streaming_client
                await self._sdk_call(client.connect_and_loop_start, 1, False, None, timeout_name="sdk_connect_timeout", default_timeout=15.0)
                await asyncio.wait_for(self._stream_ready.wait(), timeout=self._timeout("sdk_connect_timeout", 15.0))
                if not self._market_paused:
                    await asyncio.wait_for(self._subscribe_ready.wait(), timeout=self._timeout("sdk_connect_timeout", 15.0))
                self.stream_state = "PAUSED" if self._market_paused else "LIVE"
                self._emit({"type": "connection", "status": "connected", "message": "实时行情会话已重建"})
                return
            except Exception as exc:
                self.stream_connected = False
                self.subscription_ready = False
                self.stream_state = "DEGRADED"
                self._emit_error(f"行情会话重建失败（{attempt}/{self._max_rebuild_attempts}）: {self._safe_error(exc)}")
        self.stream_state = "DISCONNECTED"

    @staticmethod
    def _silence_sdk_loggers() -> None:
        # The SDK installs its own handlers and may include signed headers in
        # diagnostic records, so filtering only our handlers is not sufficient.
        for name in tuple(logging.root.manager.loggerDict):
            if name.startswith(("webull", "grpc")):
                logger = logging.getLogger(name)
                logger.disabled = True
                logger.handlers.clear()
                logger.propagate = False

    def _start_order_events(self) -> None:
        import grpc
        from webull.core.auth.algorithm import sha_hmac256_new
        import webull.trade.events.events_pb2 as pb
        import webull.trade.events.events_pb2_grpc as pb_grpc
        from webull.trade.events.signature_composer import calc_signature
        from webull.trade.trade_events_client import TradeEventsClient

        class QuietTradeEventsClient(TradeEventsClient):
            """SDK event client without its credential-bearing diagnostic prints."""

            def __init__(self, *args: Any, **kwargs: Any):
                super().__init__(*args, **kwargs)
                self.channel: Any = None

            def _build_request(self, app_key: str, app_secret: str, accounts: list[str]):
                request = pb.SubscribeRequest(
                    subscribeType=7,
                    timestamp=int(time.time() * 1000),
                    accounts=accounts,
                )
                _signature, metadata = calc_signature(
                    app_key,
                    app_secret,
                    request,
                    sha_hmac256_new,
                )
                return request, metadata

            def do_subscribe(self, accounts: list[str]):
                target = f"{self._host}:{self._port}"
                credentials = grpc.ssl_channel_credentials()
                self.channel = grpc.secure_channel(target, credentials)
                try:
                    self._stream_processing(pb_grpc.EventServiceStub(self.channel), accounts)
                finally:
                    self.channel.close()

            def stop(self) -> None:
                if self.channel is not None:
                    self.channel.close()

        client = QuietTradeEventsClient(
            self._app_key,
            self._app_secret,
            "hk",
            host="events-api.sandbox.webull.hk" if self.settings.sandbox else "events-api.webull.hk",
        )

        def on_connect(*_args: Any) -> None:
            self.events_healthy = True
            self._signal_event(self._events_ready)
            if self.connected:
                self._emit({"type": "connection", "status": "connected", "message": "账户、行情与订单事件均已连接"})

        def on_disconnect(*_args: Any) -> None:
            self.events_healthy = False
            if not self._closing:
                self._emit({"type": "connection", "status": "degraded", "message": "订单事件通道已断开，新订单已禁用"})
                self._emit_error("订单事件通道已断开，已禁止新订单")

        def on_event(event_type: Any, subscribe_type: Any, payload: Any, _raw: Any) -> None:
            from webull.trade.events.types import EVENT_TYPE_ORDER, ORDER_STATUS_CHANGED

            if event_type != EVENT_TYPE_ORDER or subscribe_type != ORDER_STATUS_CHANGED:
                return
            data = protobuf_to_dict(payload)
            self._emit({"type": "order.update", "order": data, "receivedAt": time.time()})

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_events_message = on_event
        self._events_client = client
        def run_events() -> None:
            try:
                client.do_subscribe([self.account_id])
            except Exception as exc:
                self.events_healthy = False
                if not self._closing:
                    self._logger.error("订单事件通道退出: %s", type(exc).__name__)
                    self._emit({"type": "connection", "status": "degraded", "message": "订单事件通道不可用，新订单已禁用"})

        self._events_thread = threading.Thread(
            target=run_events,
            name="fast-order-events",
            daemon=True,
        )
        self._events_thread.start()

    async def validate_symbol(self, symbol: str) -> None:
        symbol = normalize_symbol(symbol)
        if self.data_client is None:
            raise RuntimeError("券商API行情客户端未连接")
        response = await self._sdk_call(
            self.data_client.market_data.get_quotes,
            symbol,
            self.CATEGORY,
            1,
            False,
        )
        if not response_ok(response):
            raise ValueError(f"无法验证股票 {symbol}: {safe_json(response_json(response))}")
        payload = response_json(response)
        if payload in (None, [], {}) or (isinstance(payload, dict) and not rows_from(payload) and not payload.get("symbol")):
            raise ValueError(f"券商API未返回股票 {symbol} 的行情")

    async def recover_market(self, symbol: str) -> list[dict[str, Any]]:
        """Fetch a fresh snapshot and top-of-book only when the MQTT stream is quiet."""
        symbol = normalize_symbol(symbol)
        if self.data_client is None:
            raise RuntimeError("券商API行情客户端未连接")
        # Keep SDK calls sequential: its shared authenticated client is not
        # documented as thread-safe.
        snapshot_response = await self._sdk_call(
            self.data_client.market_data.get_snapshot,
            symbol,
            self.CATEGORY,
            False,
            False,
        )
        quote_response = await self._sdk_call(
            self.data_client.market_data.get_quotes,
            symbol,
            self.CATEGORY,
            1,
            False,
        )
        responses = (("snapshot", snapshot_response), ("quote", quote_response))
        received_at = time.time()
        events: list[dict[str, Any]] = []
        for kind, response in responses:
            if not response_ok(response):
                raise RuntimeError(f"{kind} 恢复校验失败: {safe_json(response_json(response))}")
            payload = response_json(response)
            rows = rows_from(payload)
            data = rows[0] if rows else payload if isinstance(payload, dict) else None
            if not isinstance(data, dict) or not data:
                continue
            event_symbol = str(data.get("symbol") or "").upper()
            if not event_symbol:
                continue
            if event_symbol != symbol:
                continue
            events.append(
                {
                    "type": kind,
                    "symbol": symbol,
                    "generation": self._stream_generation,
                    "receivedAt": received_at,
                    "source": "rest-recovery",
                    "data": data,
                }
            )
        if not events:
            raise RuntimeError(f"券商API未返回股票 {symbol} 的恢复行情")
        return events

    async def set_market_active(self, active: bool) -> None:
        if self.streaming_client is None or not self.stream_connected:
            self._market_paused = not active
            self.subscription_ready = False
            self.stream_state = "PAUSED" if not active else "DISCONNECTED"
            return
        async with self._subscription_lock:
            if active and self._market_paused:
                self._subscribe_ready = asyncio.Event()
                try:
                    self.subscription_ready = False
                    self.stream_state = "SUBSCRIBING"
                    await self._sdk_call(
                        self.streaming_client.subscribe,
                        [self.current_symbol],
                        self.CATEGORY,
                        self.SUB_TYPES,
                        1,
                        False,
                    )
                    await asyncio.wait_for(self._subscribe_ready.wait(), timeout=10)
                except Exception as exc:
                    self.stream_connected = False
                    self.subscription_ready = False
                    self.stream_state = "DEGRADED"
                    raise GatewayUnavailableError(f"恢复前台行情订阅失败: {self._safe_error(exc)}") from exc
                self._market_paused = False
                self.stream_state = "LIVE"
            elif not active and not self._market_paused:
                try:
                    await self._sdk_call(
                        self.streaming_client.unsubscribe,
                        [self.current_symbol],
                        self.CATEGORY,
                        self.SUB_TYPES,
                        False,
                    )
                except Exception:
                    self.stream_connected = False
                    self.subscription_ready = False
                    self.stream_state = "DEGRADED"
                    raise
                else:
                    self._market_paused = True
                    self.subscription_ready = False
                    self.stream_state = "PAUSED"

    async def search_instruments(self, query: str) -> list[dict[str, str]]:
        if self.data_client is None:
            raise RuntimeError("券商API行情客户端未连接")
        # Symbol-shaped searches use the direct lookup. It is fast and returns
        # historical namesakes so the UI can distinguish active and delisted rows.
        if not (1 <= len(query) <= 10 and all(char.isalnum() or char in ".-" for char in query)):
            return []
        response = await self._sdk_call(
            self.data_client.instrument.get_instrument,
            symbols=query,
            category=self.CATEGORY,
            page_size=100,
        )
        if not response_ok(response):
            raise RuntimeError(f"股票候选查询失败: {safe_json(response_json(response))}")
        return search_instruments(rows_from(response), query)

    async def switch_symbol(self, symbol: str) -> None:
        symbol = normalize_symbol(symbol)
        if not self.stream_connected or self.streaming_client is None:
            raise RuntimeError("实时行情未连接")
        if symbol == self.current_symbol:
            return
        if self._market_paused:
            raise RuntimeError("页面在后台，无法切换股票")
        await self.validate_symbol(symbol)
        previous = self.current_symbol
        async with self._subscription_lock:
            try:
                await self._sdk_call(
                    self.streaming_client.unsubscribe,
                    [previous],
                    self.CATEGORY,
                    self.SUB_TYPES,
                    False,
                )
            except Exception as exc:
                self.stream_connected = False
                self.subscription_ready = False
                self.stream_state = "DEGRADED"
                raise GatewayUnavailableError(f"取消旧行情订阅失败: {self._safe_error(exc)}") from exc
            try:
                self.current_symbol = symbol
                self._subscribe_ready = asyncio.Event()
                await self._sdk_call(
                    self.streaming_client.subscribe,
                    [symbol],
                    self.CATEGORY,
                    self.SUB_TYPES,
                    1,
                    False,
                )
                await asyncio.wait_for(self._subscribe_ready.wait(), timeout=10)
                self.subscription_ready = True
                self.stream_state = "LIVE"
            except Exception:
                self.current_symbol = previous
                self.subscription_ready = False
                try:
                    await self._sdk_call(
                        self.streaming_client.subscribe,
                        [previous],
                        self.CATEGORY,
                        self.SUB_TYPES,
                        1,
                        False,
                    )
                except Exception:
                    self.stream_connected = False
                    self.subscription_ready = False
                    self.stream_state = "DEGRADED"
                raise

    async def get_available_position(self, symbol: str) -> float:
        self._require_trade()
        response = await self._sdk_call(self.trade_client.account_v2.get_account_position, self.account_id)
        if not response_ok(response):
            raise RuntimeError(f"持仓查询失败: {safe_json(response_json(response))}")
        return available_position(self._position_rows(response_json(response)), symbol)

    async def get_positions(self) -> list[dict[str, str]]:
        """Return the small, public-safe USD stock/ETF position projection.

        The broker response contains account-scoped and instrument metadata that
        must never cross the websocket boundary.  Keep the projection here so
        callers cannot accidentally publish the raw SDK response.
        """
        self._require_trade()
        response = await self._sdk_call(self.trade_client.account_v2.get_account_position, self.account_id)
        if not response_ok(response):
            # Do not include the broker response in an exception: SDK payloads
            # may contain account identifiers and other sensitive fields.
            raise RuntimeError("持仓查询失败")
        payload = response_json(response)
        rows = self._position_rows(payload)

        positions: list[dict[str, str]] = []
        for row in rows:
            currency = str(
                row.get("currency")
                or row.get("currency_code")
                or row.get("settle_currency")
                or ""
            ).strip().upper()
            instrument_type = str(
                row.get("instrument_type")
                or row.get("instrumentType")
                or row.get("asset_type")
                or ""
            ).strip().upper()
            if currency != "USD" or instrument_type not in {"STOCK", "ETF", "EQUITY"}:
                continue
            symbol = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
            name = str(
                row.get("short_name")
                or row.get("shortName")
                or row.get("symbol_name")
                or row.get("symbolName")
                or row.get("instrument_name")
                or row.get("instrumentName")
                or row.get("security_name")
                or row.get("securityName")
                or row.get("name")
                or symbol
            ).strip()
            quantity = self._position_decimal(row, "qty", "quantity", "position_quantity")
            last_price = self._position_decimal(row, "last_price", "lastPrice", "current_price")
            unit_cost = self._position_decimal(row, "unit_cost", "unitCost", "cost_price", "avg_cost")
            unrealized_pnl = self._position_decimal(
                row,
                "unrealized_profit_loss",
                "unrealizedPnl",
                "unrealized_pnl",
            )
            if not symbol or quantity is None or quantity == 0:
                continue
            if last_price is None or unit_cost is None or unrealized_pnl is None:
                continue
            positions.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "lastPrice": self._position_number_text(last_price),
                    "quantity": self._position_number_text(quantity),
                    "unitCost": self._position_number_text(unit_cost),
                    "unrealizedPnl": self._position_number_text(unrealized_pnl),
                }
            )
        return positions

    @staticmethod
    def _position_rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            for container in (payload, payload.get("data"), payload.get("result")):
                if not isinstance(container, dict):
                    continue
                holdings = container.get("holdings")
                if isinstance(holdings, list):
                    return [item for item in holdings if isinstance(item, dict)]
        return rows_from(payload)

    @staticmethod
    def _position_decimal(row: dict[str, Any], *keys: str) -> Decimal | None:
        for key in keys:
            value = row.get(key)
            if value in (None, ""):
                continue
            try:
                number = Decimal(str(value).replace(",", "").strip())
            except (InvalidOperation, TypeError, ValueError):
                continue
            if number.is_finite():
                return number
        return None

    @staticmethod
    def _position_number_text(value: Decimal) -> str:
        if value == 0:
            return "0"
        return format(value.normalize(), "f")

    async def get_usd_buying_power(self) -> str | None:
        """Return only the USD buying-power field from the account balance."""
        if self.trade_client is None or not self.account_id:
            raise GatewayUnavailableError("券商API交易账户未连接")
        response = await self._sdk_call(
            self.trade_client.account_v2.get_account_balance,
            self.account_id,
        )
        if not response_ok(response):
            raise RuntimeError("美元购买力查询失败")
        payload = response_json(response)
        if not isinstance(payload, dict):
            return None
        assets = payload.get("account_currency_assets") or payload.get("accountCurrencyAssets") or []
        if isinstance(assets, dict):
            assets = [assets]
        for asset in assets if isinstance(assets, list) else []:
            if not isinstance(asset, dict) or str(asset.get("currency") or "").upper() != "USD":
                continue
            raw = asset.get("buying_power")
            if raw is None:
                raw = asset.get("buyingPower")
            if raw in (None, ""):
                return None
            try:
                amount = Decimal(str(raw))
            except (InvalidOperation, ValueError):
                return None
            return format(amount, "f") if amount.is_finite() else None
        return None

    async def place_order(self, order: dict[str, Any]) -> Any:
        self._require_trade(require_market=True)
        payload: dict[str, Any] = {
            "client_order_id": order["clientOrderId"],
            "combo_type": "NORMAL",
            "symbol": order["symbol"],
            "instrument_type": "EQUITY",
            "market": "US",
            "order_type": order["orderType"],
            "quantity": str(order["quantity"]),
            "side": order["side"],
            "time_in_force": "DAY",
            "support_trading_session": "CORE",
            "entrust_type": "QTY",
        }
        if order["orderType"] == "LIMIT":
            payload["limit_price"] = order["limitPrice"]
        try:
            response = await self._sdk_call(
                self.trade_client.order_v3.place_order,
                self.account_id,
                [payload],
                timeout_name="sdk_total_timeout",
                default_timeout=30.0,
            )
        except (TimeoutError, asyncio.TimeoutError, OSError) as exc:
            raise OrderUncertainError("下单请求超时或连接中断，必须按 client_order_id 对账，禁止重发") from exc
        except Exception as exc:
            raise OrderUncertainError("下单请求结果不确定，必须按 client_order_id 对账，禁止重发") from exc
        if not response_ok(response):
            raise self._order_response_error(response)
        return response_json(response)

    async def get_order_detail(self, client_order_id: str) -> Any | None:
        self._require_trade()
        response = await self._sdk_call(
            self.trade_client.order_v3.get_order_detail,
            self.account_id,
            client_order_id,
        )
        return response_json(response) if response_ok(response) else None

    async def cancel_order(self, client_order_id: str) -> Any:
        self._require_trade()
        try:
            response = await self._sdk_call(
                self.trade_client.order_v3.cancel_order,
                self.account_id,
                client_order_id,
            )
        except Exception as exc:
            raise OrderUncertainError("撤单请求结果不确定，必须按订单 ID 对账，禁止重发") from exc
        if not response_ok(response):
            raise self._order_response_error(response, action="撤单")
        return response_json(response)

    async def get_open_orders(self) -> list[dict[str, Any]]:
        """Return pending orders for startup reconciliation (read-only)."""
        self._require_trade()
        operation = getattr(self.trade_client.order_v3, "get_order_open", None)
        if not callable(operation):
            return []
        response = await self._sdk_call(operation, self.account_id, page_size=100)
        if not response_ok(response):
            raise RuntimeError(f"未成交订单查询失败: {safe_json(response_json(response))}")
        return rows_from(response)

    @staticmethod
    def _status_code(response: Any) -> int:
        try:
            return int(getattr(response, "status_code", 200) or 200)
        except (TypeError, ValueError):
            return 200

    @classmethod
    def _order_response_error(cls, response: Any, action: str = "下单") -> RuntimeError:
        status = cls._status_code(response)
        message = safe_json(response_json(response))
        if status == 429 or status >= 500:
            return OrderUncertainError(f"{action}响应异常（HTTP {status}），必须按订单 ID 对账，禁止重发")
        return BrokerRejectedError(f"券商API拒绝{action}: {message}")

    async def close(self) -> None:
        self._closing = True
        self._market_paused = True
        self.connected = False
        self.stream_connected = False
        self.subscription_ready = False
        self.events_healthy = False
        self.stream_state = "DISCONNECTED"
        rebuild = self._stream_rebuild_task
        self._stream_rebuild_task = None
        if rebuild is not None and not rebuild.done():
            rebuild.cancel()
        client, self.streaming_client = self.streaming_client, None
        events_client, self._events_client = self._events_client, None
        if events_client is not None:
            try:
                await self._sdk_call(events_client.stop, timeout_name="sdk_total_timeout", default_timeout=10.0)
            except Exception:
                pass
        if self._events_thread is not None and self._events_thread.is_alive():
            await asyncio.to_thread(self._events_thread.join, 5.0)
        self._events_thread = None
        if client is not None:
            await self._shutdown_stream_client(client)
        self.trade_client = None
        self.data_client = None
        self.account_id = ""
        self._discovered_account_ids.clear()

    async def _shutdown_stream_client(self, client: Any) -> None:
        if client is None:
            return
        try:
            await self._sdk_call(client.unsubscribe, unsubscribe_all=True, timeout_name="sdk_total_timeout", default_timeout=10.0)
        except Exception:
            pass
        for method_name in ("disconnect_and_loop_stop", "disconnect"):
            method = getattr(client, method_name, None)
            if callable(method):
                try:
                    await self._sdk_call(method, timeout_name="sdk_total_timeout", default_timeout=10.0)
                except Exception:
                    pass
                break

    def _handle_market_message(self, topic: str, payload: Any, generation: int | None = None) -> None:
        if generation is not None and generation != self._stream_generation:
            return
        data = protobuf_to_dict(payload)
        topic_name = topic.lower()
        if "snapshot" in topic_name:
            kind = "snapshot"
        elif "tick" in topic_name:
            kind = "tick"
        elif "quote" in topic_name:
            kind = "quote"
        else:
            return
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict):
                continue
            basic = item.get("basic") if isinstance(item.get("basic"), dict) else {}
            symbol = str(basic.get("symbol") or item.get("symbol") or "").upper()
            if not symbol:
                continue
            if symbol != self.current_symbol:
                continue
            # A valid event for the requested symbol is stronger evidence than
            # an optional/late subscribe acknowledgement from the SDK.
            self.subscription_ready = True
            self.stream_state = "LIVE"
            self._signal_event(self._subscribe_ready)
            event = {
                "type": kind,
                "symbol": symbol,
                "generation": self._stream_generation,
                "receivedAt": time.time(),
                "data": item,
            }
            self._emit(event)

    def _signal_event(self, event: asyncio.Event | None) -> None:
        if self._loop is not None and event is not None:
            self._loop.call_soon_threadsafe(event.set)

    def _emit(self, event: dict[str, Any]) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.event_sink, event)

    def _emit_error(self, message: str) -> None:
        self._logger.error(message)
        self._emit({"type": "error", "message": message})

    def _require_trade(self, require_market: bool = False) -> None:
        if self.trade_client is None or not self.account_id:
            raise GatewayUnavailableError("券商API交易账户未连接")
        if require_market and (
            not self.connected
            or not self.stream_connected
            or not self.subscription_ready
            or self._market_paused
            or not self.events_healthy
        ):
            raise GatewayUnavailableError("实时行情或订单事件通道未就绪，暂时禁止交易")

    def _masked_account_id(self) -> str:
        if len(self.account_id) <= 6:
            return "***"
        return f"{self.account_id[:3]}***{self.account_id[-3:]}"

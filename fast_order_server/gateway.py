from __future__ import annotations

import asyncio
import logging
import os
import stat
import threading
import time
import uuid
from collections.abc import Callable
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fast_order.errors import BrokerRejectedError, OrderUncertainError
from fast_order.utils import available_position, find_number, find_symbol, protobuf_to_dict, response_json, response_ok, rows_from


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    app_key: str
    app_secret: str
    token_dir: Path
    api_host: str = "api.webull.hk"
    data_host: str = "data-api.webull.hk"
    events_host: str = "events-api.webull.hk"
    sdk_timeout: float = 20.0
    quote_stale_seconds: float = 8.0


class TokenBucket:
    def __init__(self, rate: float = 15.0, capacity: float = 15.0):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.updated = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


class InstitutionalWebullGateway:
    """One credential set, many accounts, one market and one event connection."""

    CATEGORY = "US_STOCK"
    SUB_TYPES = ["QUOTE", "SNAPSHOT", "TICK"]

    def __init__(self, config: GatewayConfig, event_sink: Callable[[dict[str, Any]], None] | None = None):
        self.config = config
        self.event_sink = event_sink or (lambda _event: None)
        self.trade_client: Any = None
        self.data_client: Any = None
        self.streaming_client: Any = None
        self.events_client: Any = None
        self.events_healthy = False
        self.market_healthy = False
        self.account_ids: tuple[str, ...] = ()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._events_thread: threading.Thread | None = None
        self._market_cache: dict[str, tuple[Decimal, float]] = {}
        self._subscribed: set[str] = set()
        self._symbol_references: defaultdict[str, int] = defaultdict(int)
        self._reference_symbols: dict[str, str] = {}
        self._subscription_lock = asyncio.Lock()
        self._rate = TokenBucket()
        self._logger = logging.getLogger("fast_order_server.broker")

    async def initialize(self) -> tuple[str, ...]:
        self._loop = asyncio.get_running_loop()
        self._validate_token_dir()
        await self._call(self._build_clients)
        response = await self._call(self.trade_client.account_v2.get_account_list)
        if not response_ok(response):
            raise RuntimeError("机构凭据无法读取账户列表")
        identifiers: list[str] = []
        for row in rows_from(response):
            value = row.get("account_id") or row.get("accountId") or row.get("id")
            if value:
                identifiers.append(str(value))
        if not identifiers:
            raise RuntimeError("机构凭据下没有可用账户")
        self.account_ids = tuple(dict.fromkeys(identifiers))
        self._start_trade_events(self.account_ids)
        await self._start_market()
        return self.account_ids

    def _validate_token_dir(self) -> None:
        path = self.config.token_dir
        if not path.exists():
            path.mkdir(parents=True, mode=0o700)
        for managed in (path, *path.rglob("*")):
            info = managed.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise RuntimeError("Webull token目录包含符号链接")
            if info.st_uid != os.getuid():
                raise RuntimeError("Webull token目录包含非当前UID所有的路径")
            expected = 0o700 if stat.S_ISDIR(info.st_mode) else 0o600
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise RuntimeError("Webull token目录包含不安全文件类型")
            if stat.S_IMODE(info.st_mode) != expected:
                raise RuntimeError(f"Webull token路径权限必须为 {expected:o}")

    def _build_clients(self) -> None:
        from webull.core.client import ApiClient
        from webull.data.data_client import DataClient
        from webull.trade.trade_client import TradeClient

        self._silence_sdk_logs()
        api = ApiClient(self.config.app_key, self.config.app_secret, "hk")
        if self.config.api_host != "api.webull.hk":
            api.add_endpoint("hk", self.config.api_host)
        api._stream_logger_set = True
        api._file_logger_set = True
        api.set_token_dir(str(self.config.token_dir.resolve()))
        self.trade_client = TradeClient(api)
        self.data_client = DataClient(api)

    async def _start_market(self) -> None:
        from webull.data.data_streaming_client import DataStreamingClient

        gateway = self

        class QuietStreamingClient(DataStreamingClient):
            def connect_and_loop_forever(self, timeout: float = 1, logger_enable: bool = True, customer_logger: Any = None) -> Any:
                return super().connect_and_loop_forever(timeout=timeout, logger_enable=False, customer_logger=None)

        client = QuietStreamingClient(
            self.config.app_key, self.config.app_secret, "hk", f"fos-{uuid.uuid4().hex[:16]}",
            http_host=self.config.api_host, mqtt_host=self.config.data_host,
        )
        api = getattr(client, "api_client", None)
        if api is not None:
            api._stream_logger_set = True
            api._file_logger_set = True
            api.set_token_dir(str(self.config.token_dir.resolve()))

        def connected(*_args: Any) -> None:
            gateway.market_healthy = True
            if gateway._loop and gateway._subscribed:
                asyncio.run_coroutine_threadsafe(gateway._subscribe_many(tuple(gateway._subscribed)), gateway._loop)

        def disconnected(*_args: Any) -> None:
            gateway.market_healthy = False

        def message(_client: Any, _topic: Any, payload: Any) -> None:
            gateway._receive_market(payload)

        client.on_connect_success = connected
        client.on_disconnect = disconnected
        client.on_connect_fail = disconnected
        client.on_quotes_message = message
        self.streaming_client = client
        await self._call(client.connect_and_loop_start, 1, False, None)

    async def select_symbol(self, reference_id: str, symbol: str) -> None:
        symbol = str(symbol).upper()
        async with self._subscription_lock:
            previous = self._reference_symbols.get(reference_id)
            if previous == symbol:
                return
            if previous:
                self._symbol_references[previous] -= 1
                if self._symbol_references[previous] <= 0:
                    del self._symbol_references[previous]
                    self._subscribed.discard(previous)
                    if self.market_healthy and self.streaming_client is not None:
                        await self._call(self.streaming_client.unsubscribe, [previous], self.CATEGORY, self.SUB_TYPES, False)
            self._reference_symbols[reference_id] = symbol
            first = self._symbol_references[symbol] == 0
            self._symbol_references[symbol] += 1
            self._subscribed.add(symbol)
            if first and self.market_healthy:
                await self._subscribe_many((symbol,))

    async def _subscribe_many(self, symbols: tuple[str, ...]) -> None:
        if not symbols or self.streaming_client is None:
            return
        await self._call(self.streaming_client.subscribe, list(symbols), self.CATEGORY, self.SUB_TYPES, 1, False)

    def _receive_market(self, payload: Any) -> None:
        converted = protobuf_to_dict(payload)
        for item in self._dicts(converted):
            symbol = find_symbol(item)
            raw = find_number(item, {"close", "price", "last_price", "lastprice"})
            try:
                price = Decimal(str(raw))
            except (InvalidOperation, TypeError):
                continue
            if symbol and price.is_finite() and price > 0:
                self._market_cache[symbol] = (price, time.time())

    async def fresh_price(self, symbol: str, max_age: float) -> Decimal | None:
        symbol = str(symbol).upper()
        cached = self._market_cache.get(symbol)
        if cached and time.time() - cached[1] <= max_age:
            return cached[0]
        if self.data_client is None:
            return None
        response = await self._call(self.data_client.market_data.get_snapshot, symbol, self.CATEGORY, False, False)
        if not response_ok(response):
            return None
        self._receive_market(response_json(response))
        cached = self._market_cache.get(symbol)
        return cached[0] if cached and time.time() - cached[1] <= max_age else None

    async def get_available_position(self, account_id: str, symbol: str) -> Decimal | None:
        response = await self._call(self.trade_client.account_v2.get_account_position, account_id)
        if not response_ok(response):
            return None
        value = available_position(self._position_rows(response_json(response)), symbol)
        return Decimal(str(value)) if value is not None else None

    async def place_order(self, account_id: str, order: dict[str, Any]) -> Any:
        payload = self._payload(order)
        await self._rate.acquire()
        try:
            response = await self._call(self.trade_client.order_v3.place_order, account_id, [payload])
        except Exception as exc:
            raise OrderUncertainError("下单结果不确定，必须按 client_order_id 对账") from exc
        if not response_ok(response):
            raise BrokerRejectedError("券商拒绝下单")
        return response_json(response)

    async def replace_order(self, account_id: str, order: dict[str, Any]) -> Any:
        payload = self._payload(order)
        if payload["order_type"] != "LIMIT":
            raise ValueError("第一阶段只允许修改限价单价格")
        await self._rate.acquire()
        try:
            response = await self._call(self.trade_client.order_v3.replace_order, account_id, [payload])
        except Exception as exc:
            raise OrderUncertainError("改单结果不确定，必须按 client_order_id 对账") from exc
        if not response_ok(response):
            raise BrokerRejectedError("券商拒绝改单")
        return response_json(response)

    async def cancel_order(self, account_id: str, client_order_id: str) -> Any:
        await self._rate.acquire()
        try:
            response = await self._call(self.trade_client.order_v3.cancel_order, account_id, client_order_id)
        except Exception as exc:
            raise OrderUncertainError("撤单结果不确定，必须按 client_order_id 对账") from exc
        if not response_ok(response):
            raise BrokerRejectedError("券商拒绝撤单")
        return response_json(response)

    async def get_order_detail(self, account_id: str, client_order_id: str) -> Any | None:
        response = await self._call(self.trade_client.order_v3.get_order_detail, account_id, client_order_id)
        return response_json(response) if response_ok(response) else None

    @staticmethod
    def _payload(order: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "client_order_id": order["clientOrderId"], "combo_type": "NORMAL", "symbol": order["symbol"],
            "instrument_type": "EQUITY", "market": "US", "order_type": order["orderType"],
            "quantity": str(order["quantity"]), "side": order["side"], "time_in_force": "DAY",
            "support_trading_session": "CORE", "entrust_type": "QTY",
        }
        if order["orderType"] == "LIMIT":
            payload["limit_price"] = order["limitPrice"]
        return payload

    def _start_trade_events(self, accounts: tuple[str, ...]) -> None:
        import grpc
        from webull.core.auth.algorithm import sha_hmac256_new
        import webull.trade.events.events_pb2 as pb
        import webull.trade.events.events_pb2_grpc as pb_grpc
        from webull.trade.events.signature_composer import calc_signature
        from webull.trade.trade_events_client import TradeEventsClient

        class QuietTradeEventsClient(TradeEventsClient):
            """Avoid the SDK diagnostic path which can emit signed metadata."""

            def __init__(self, *args: Any, **kwargs: Any):
                super().__init__(*args, **kwargs)
                self.channel: Any = None

            def _build_request(self, app_key: str, app_secret: str, account_ids: list[str]):
                request = pb.SubscribeRequest(subscribeType=7, timestamp=int(time.time() * 1000), accounts=account_ids)
                _signature, metadata = calc_signature(app_key, app_secret, request, sha_hmac256_new)
                return request, metadata

            def do_subscribe(self, account_ids: list[str]):
                self.channel = grpc.secure_channel(f"{self._host}:{self._port}", grpc.ssl_channel_credentials())
                try:
                    self._stream_processing(pb_grpc.EventServiceStub(self.channel), account_ids)
                finally:
                    self.channel.close()

            def stop(self) -> None:
                if self.channel is not None:
                    self.channel.close()

        client = QuietTradeEventsClient(self.config.app_key, self.config.app_secret, "hk", host=self.config.events_host)

        def connected(*_args: Any) -> None:
            self.events_healthy = True

        def disconnected(*_args: Any) -> None:
            self.events_healthy = False

        def event(event_type: Any, subscribe_type: Any, payload: Any, _raw: Any) -> None:
            data = protobuf_to_dict(payload)
            self.event_sink({"type": "broker.order", "data": data, "receivedAt": time.time()})

        client.on_connect = connected
        client.on_disconnect = disconnected
        client.on_events_message = event
        self.events_client = client

        def run() -> None:
            try:
                client.do_subscribe(list(accounts))
            except Exception:
                self.events_healthy = False
                self._logger.error("Webull订单事件通道退出")

        self._events_thread = threading.Thread(target=run, name="fast-order-server-events", daemon=True)
        self._events_thread.start()

    async def _call(self, function: Callable[..., Any], *args: Any) -> Any:
        try:
            return await asyncio.wait_for(asyncio.to_thread(function, *args), timeout=self.config.sdk_timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("券商API请求超时") from exc

    async def close(self) -> None:
        self.events_healthy = False
        self.market_healthy = False
        if self.events_client is not None:
            stop = getattr(self.events_client, "stop", None)
            if callable(stop):
                stop()
        if self.streaming_client is not None:
            for name in ("disconnect_and_loop_stop", "disconnect"):
                operation = getattr(self.streaming_client, name, None)
                if callable(operation):
                    try:
                        await self._call(operation)
                    except Exception:
                        pass
                    break

    @staticmethod
    def _silence_sdk_logs() -> None:
        for name in tuple(logging.root.manager.loggerDict):
            if str(name).startswith(("webull", "grpc")):
                logger = logging.getLogger(str(name))
                logger.disabled = True
                logger.handlers.clear()
                logger.propagate = False

    @staticmethod
    def _dicts(value: Any):
        if isinstance(value, dict):
            yield value
            for nested in value.values():
                yield from InstitutionalWebullGateway._dicts(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from InstitutionalWebullGateway._dicts(nested)

    @staticmethod
    def _position_rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            for container in (payload, payload.get("data"), payload.get("result")):
                if isinstance(container, dict) and isinstance(container.get("holdings"), list):
                    return [item for item in container["holdings"] if isinstance(item, dict)]
        return rows_from(payload)

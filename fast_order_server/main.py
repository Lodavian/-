from __future__ import annotations

import asyncio
import base64
import logging
import os
import signal
import stat
import sys
from decimal import Decimal
from pathlib import Path

from aiohttp import web

from .admin import AdminService
from .api import ServerApi
from .casdoor import CasdoorAdminClient, CasdoorAdminConfig
from .controls import ControlPlane
from .gateway import GatewayConfig, InstitutionalWebullGateway
from .models import RiskLimits
from .postgres import AccountCipher, PostgresStore
from .security import CasdoorJwtVerifier, RequestAuthenticator
from .service import InstitutionalTradingService


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少必需配置 {name}")
    return value


def _secret_file(name: str) -> str:
    path = Path(_required(name))
    parent = path.parent.lstat()
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
        raise RuntimeError(f"密钥目录 {name} 类型不安全")
    if os.name != "nt" and (parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700):
        raise RuntimeError(f"密钥目录 {name} 必须属于当前UID且权限为0700")
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"密钥文件 {name} 类型不安全")
    if os.name != "nt" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise RuntimeError(f"密钥文件 {name} 必须属于当前UID且权限为0600")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"密钥文件 {name} 为空")
    return value


def _decimal(name: str) -> Decimal:
    value = Decimal(_required(name))
    if not value.is_finite() or value <= 0:
        raise RuntimeError(f"{name} 必须为正数")
    return value


def _integer(name: str) -> int:
    value = int(_required(name))
    if value <= 0:
        raise RuntimeError(f"{name} 必须为正整数")
    return value


async def build_application() -> tuple[web.Application, InstitutionalWebullGateway, PostgresStore]:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("机构服务器入口只允许在 Linux 上运行")
    if os.environ.get("FASTORDER_LIVE_ALLOWED", "").lower() != "true":
        raise RuntimeError("服务器版只允许显式 FASTORDER_LIVE_ALLOWED=true 启动")
    app_key = _secret_file("FASTORDER_WEBULL_APP_KEY_FILE")
    app_secret = _secret_file("FASTORDER_WEBULL_APP_SECRET_FILE")
    database_dsn = _secret_file("FASTORDER_DATABASE_DSN_FILE")
    encoded_key = _secret_file("FASTORDER_ACCOUNT_KEY_FILE")
    master_key = base64.urlsafe_b64decode(encoded_key + "=" * (-len(encoded_key) % 4))
    casdoor_secret = _secret_file("FASTORDER_CASDOOR_CLIENT_SECRET_FILE")

    store = await PostgresStore.connect(database_dsn, AccountCipher(master_key))
    await store.migrate()
    controls = ControlPlane(await store.load_controls(force_global_off=True))
    gateway = InstitutionalWebullGateway(
        GatewayConfig(
            app_key=app_key,
            app_secret=app_secret,
            token_dir=Path(os.environ.get("FASTORDER_TOKEN_DIR", "/var/lib/fastorder/webull_tokens")),
        )
    )
    discovered = set(await gateway.initialize())
    stale_accounts = await store.registered_broker_account_ids() - discovered
    if stale_accounts:
        raise RuntimeError("数据库包含当前机构凭据无法发现的账户，拒绝启动")
    limits = RiskLimits(
        max_order_qty=_integer("FASTORDER_MAX_ORDER_QTY"),
        max_order_value=_decimal("FASTORDER_MAX_ORDER_VALUE"),
        max_market_qty=_integer("FASTORDER_MAX_MARKET_QTY"),
        max_market_estimated_value=_decimal("FASTORDER_MAX_MARKET_ESTIMATED_VALUE"),
        max_daily_gross_notional=_decimal("FASTORDER_MAX_DAILY_GROSS_NOTIONAL"),
        max_open_orders=_integer("FASTORDER_MAX_OPEN_ORDERS"),
        quote_stale_seconds=float(_required("FASTORDER_QUOTE_STALE_SECONDS")),
    )
    service = InstitutionalTradingService(controls, store, gateway, limits=limits)
    loop = asyncio.get_running_loop()
    gateway.event_sink = lambda event: loop.call_soon_threadsafe(
        asyncio.create_task, service.receive_broker_event(event)
    )
    await service.reconcile_unresolved()
    casdoor_base = _required("FASTORDER_CASDOOR_URL").rstrip("/")
    issuer = _required("FASTORDER_CASDOOR_ISSUER")
    audience = _required("FASTORDER_CASDOOR_CLIENT_ID")
    token_verifier = CasdoorJwtVerifier(_required("FASTORDER_CASDOOR_JWKS_URL"), issuer, audience)
    await asyncio.to_thread(token_verifier.refresh)
    authenticator = RequestAuthenticator(
        token_verifier, controls, subject_claim=os.environ.get("FASTORDER_CASDOOR_SUBJECT_CLAIM", "sub")
    )
    casdoor = CasdoorAdminClient(
        CasdoorAdminConfig(
            base_url=casdoor_base,
            client_id=audience,
            client_secret=casdoor_secret,
            organization=_required("FASTORDER_CASDOOR_ORGANIZATION"),
            application=_required("FASTORDER_CASDOOR_APPLICATION"),
        )
    )
    admin = AdminService(store, controls, casdoor, discovered)
    api = ServerApi(
        service, controls, authenticator, token_verifier,
        register_device=store.register_device,
        set_control=store.set_control,
        set_device_enabled=store.set_device_enabled,
        admin_service=admin,
        admin_static_dir=Path(os.environ.get("FASTORDER_ADMIN_STATIC_DIR", "/app/server_admin")),
        admin_oidc_config={
            "authorizationEndpoint": _required("FASTORDER_CASDOOR_AUTHORIZATION_ENDPOINT"),
            "tokenEndpoint": _required("FASTORDER_CASDOOR_TOKEN_ENDPOINT"),
            "clientId": audience,
            "redirectUri": _required("FASTORDER_ADMIN_REDIRECT_URI"),
            "scope": "openid profile",
        },
        database_healthy=store.healthy,
        record_login=store.record_login,
        record_rejection=store.record_rejection,
    )
    application = api.application()

    async def refresh_jwks(_application: web.Application):
        async def worker() -> None:
            while True:
                await asyncio.sleep(token_verifier.refresh_seconds)
                try:
                    await asyncio.to_thread(token_verifier.refresh)
                except Exception:
                    logging.getLogger("fast_order_server.auth").warning("身份系统签名密钥后台刷新失败")
        task = asyncio.create_task(worker(), name="fastorder-jwks-refresh")
        yield
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    application.cleanup_ctx.append(refresh_jwks)
    return application, gateway, store


async def serve() -> None:
    os.umask(0o077)
    logging.basicConfig(level=os.environ.get("FASTORDER_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    application, gateway, store = await build_application()
    runner = web.AppRunner(application, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("FASTORDER_PORT", "8080")))
    await site.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        await stop.wait()
    finally:
        await runner.cleanup()
        await gateway.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(serve())

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

import app as app_module


class FakeGateway:
    connected = False
    stream_connected = False
    current_symbol = "AAPL"

    def __init__(self, _settings, _callback):
        pass

    async def set_market_active(self, _active):
        pass

    async def close(self):
        pass


class FakeService:
    connection_status = "offline"
    current_symbol = "AAPL"
    market_active = False
    live_armed = False

    def __init__(self, _settings, _gateway, _publisher, *, market_active=False):
        self.market_active = market_active

    def initial_state(self):
        return {
            "type": "state",
            "symbol": "AAPL",
            "orders": [],
            "limits": {"maxQty": 1000, "maxValue": 25000, "staleSeconds": 8},
            "connection": self.connection_event(),
        }

    def connection_event(self):
        return {
            "type": "connection",
            "status": self.connection_status,
            "sandbox": True,
            "tradingEnabled": False,
        }

    async def set_market_active(self, active):
        self.market_active = active

    async def maintain_market_freshness(self):
        return False

    def arm_live(self):
        self.live_armed = True
        return self.risk_event()

    def disarm_live(self, _reason=""):
        self.live_armed = False
        return self.risk_event()

    def risk_event(self):
        return {"type": "risk.state", "liveArmed": self.live_armed}

    async def close(self):
        pass

    def livez(self):
        return {"ok": True, "status": "alive"}

    def readyz(self):
        return {"ok": False, "status": "not_ready"}


def make_settings():
    return SimpleNamespace(port=8765, sandbox=True, default_symbol="AAPL")


def test_outbound_events_mask_account_identifiers():
    event = app_module.FastOrderApplication._sanitize_outbound(
        {"accountId": "VPH1234567NA", "risk": {"account_id": "VPH1234567NA"}, "symbol": "AAPL"}
    )
    assert event["accountId"] == "••••67NA"
    assert event["risk"]["account_id"] == "••••67NA"
    assert event["symbol"] == "AAPL"
    masked = app_module.FastOrderApplication._sanitize_outbound({"accountId": "abc***xyz"})
    assert masked["accountId"] == "abc***xyz"


def test_hidden_regions_cannot_be_overridden_by_component_layout():
    styles = (Path(app_module.__file__).parent / "static" / "styles.css").read_text(encoding="utf-8")
    assert "[hidden] { display: none !important; }" in styles


def test_account_binding_ui_keeps_secret_out_of_browser_storage():
    static = Path(app_module.__file__).parent / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    script = (static / "app.js").read_text(encoding="utf-8")
    assert 'id="accountBindingDialog"' in html
    assert 'role="dialog" aria-modal="true"' in html
    assert 'id="bindingPasswordSecret" type="password" autocomplete="new-password"' in html
    assert 'id="bindingAccountCandidates"' in html
    assert 'id="forgetAccountConfirm"' in html
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert '$("bindingPasswordSecret").value = ""' in script


def test_visible_ui_uses_generic_broker_name_and_preserves_live_mode():
    static = Path(app_module.__file__).parent / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    script = (static / "app.js").read_text(encoding="utf-8")
    assert "webull" not in html.lower()
    assert "webull" not in script.lower()
    assert 'if (typeof data.sandbox === "boolean") state.sandbox = data.sandbox;' in script
    assert '$("modeBadge").textContent = state.sandbox ? "SANDBOX" : "LIVE · 实盘";' in script
    assert html.index('id="headerUsdBuyingPower"') < html.index('id="modeBadge"')
    assert "risk.usdBuyingPower" in script


def test_positions_and_manual_trade_switch_use_safe_local_protocol():
    static = Path(app_module.__file__).parent / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    script = (static / "app.js").read_text(encoding="utf-8")
    assert 'class="glass card positions-card"' in html
    for label in ("股票代码", "股票名称", "当前单价", "股数", "每股成本价", "未实现盈亏"):
        assert label in html
    assert 'id="liveToggle"' in html
    assert 'role="switch"' in html
    assert 'aria-checked="false"' in html
    assert "livePhrase" not in html + script
    assert 'send("positions.refresh"' not in script
    assert 'send(state.liveArmed ? "live.disarm" : "live.arm", {})' in script
    assert ".style." not in script
    assert "style=" not in script


def test_live_launcher_supplies_required_non_sensitive_risk_caps():
    project = Path(app_module.__file__).parent
    launcher = (project / "run-live.ps1").read_text(encoding="utf-8")
    assert 'FAST_ORDER_MAX_MARKET_QTY = "100"' in launcher
    assert 'FAST_ORDER_MAX_MARKET_ESTIMATED_VALUE = "5000"' in launcher
    assert 'FAST_ORDER_MAX_DAILY_GROSS_NOTIONAL = "25000"' in launcher
    assert 'FAST_ORDER_MAX_OPEN_ORDERS = "1"' in launcher


def test_outbound_sanitizer_redacts_credential_fields():
    value = app_module.FastOrderApplication._sanitize_outbound(
        {"appKey": "key-value", "appSecret": "secret-value", "access_token": "token-value"}
    )
    assert value == {"appKey": "***", "appSecret": "***", "access_token": "***"}


@pytest_asyncio.fixture
async def local_app(monkeypatch):
    monkeypatch.setattr(app_module, "WebullGateway", FakeGateway)
    monkeypatch.setattr(app_module, "TradingService", FakeService)
    application = app_module.FastOrderApplication(make_settings())
    server = TestServer(application.app)
    await server.start_server()
    application.settings.port = server.port
    client = TestClient(server)
    await client.start_server()
    try:
        yield application, client
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_health_probes_and_security_headers(local_app):
    _application, client = local_app
    response = await client.get("/")
    assert response.status == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "fast-order-session-token" in await response.text()

    assert (await client.get("/livez")).status == 200
    assert (await client.get("/readyz")).status == 503


@pytest.mark.asyncio
async def test_websocket_requires_protocol_token_and_marks_second_tab_readonly(local_app):
    application, client = local_app
    token_protocol = f"fast-order-session.{application.session_token}"
    origin = f"http://127.0.0.1:{application.settings.port}"

    with pytest.raises(Exception):
        await client.ws_connect("/ws", headers={"Origin": origin})

    first = await client.ws_connect("/ws", protocols=[token_protocol], headers={"Origin": origin})
    initial = await first.receive_json()
    assert initial["connection"]["controllerRole"] == "controller"

    second = await client.ws_connect("/ws", protocols=[token_protocol], headers={"Origin": origin})
    second_initial = await second.receive_json()
    assert second_initial["connection"]["readOnly"] is True

    await second.send_json({"type": "page.visibility", "payload": {"visible": True}})
    await asyncio.sleep(0.02)
    assert application.service.market_active is True
    await second.send_json({"type": "page.visibility", "payload": {"visible": False}})
    await asyncio.sleep(0.02)
    assert application.service.market_active is False

    await second.send_json({"type": "connect", "payload": {}})
    error = await second.receive_json()
    assert error["type"] == "error"
    assert "只读" in error["message"]
    await first.close()
    await second.close()


@pytest.mark.asyncio
async def test_trade_switch_commands_require_empty_payload(local_app):
    application, client = local_app
    token_protocol = f"fast-order-session.{application.session_token}"
    origin = f"http://127.0.0.1:{application.settings.port}"
    socket = await client.ws_connect("/ws", protocols=[token_protocol], headers={"Origin": origin})
    await socket.receive_json()

    await socket.send_json({"type": "live.arm", "payload": {"unexpected": "value"}})
    rejected = await socket.receive_json()
    assert rejected["type"] == "error"
    await socket.send_json({"type": "live.arm", "payload": {}})
    armed = await socket.receive_json()
    assert armed["type"] == "risk.state"
    assert armed["liveArmed"] is True
    await socket.send_json({"type": "live.disarm", "payload": {}})
    disarmed = await socket.receive_json()
    assert disarmed["type"] == "risk.state"
    assert disarmed["liveArmed"] is False
    await socket.close()

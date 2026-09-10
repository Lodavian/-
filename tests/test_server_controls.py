from __future__ import annotations

import asyncio
import base64
import time
import uuid
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from fast_order_server.controls import AccessDenied, ControlPlane, ControlSnapshot
from fast_order_server.models import AccountGrant, DeviceGrant, Principal, RiskLimits, UserGrant
from fast_order_server.security import RequestAuthenticator, RequestProof, StaticTokenVerifier, canonical_request
from fast_order_server.service import InstitutionalTradingService
from fast_order_server.store import MemoryOrderStore


def make_controls(*, global_enabled: bool = True, user_trade: bool = True, account_trade: bool = True):
    private = ec.generate_private_key(ec.SECP256R1())
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")
    snapshot = ControlSnapshot.frozen(
        global_trade_enabled=global_enabled,
        version=1,
        users={"user-1": UserGrant("user-1", True, user_trade)},
        devices={"device-1": DeviceGrant("device-1", "user-1", public_pem, True)},
        accounts={
            "account-ref": AccountGrant(
                "account-ref", "user-1", "REAL-ACCOUNT-123", "自营一号", "••••0123", True, account_trade
            )
        },
    )
    return ControlPlane(snapshot), private


class FakeInstitutionalGateway:
    events_healthy = True

    def __init__(self):
        self.calls = []
        self.delay = 0.0

    async def fresh_price(self, symbol, max_age):
        return Decimal("100")

    async def get_available_position(self, account_id, symbol):
        return Decimal("1000")

    async def place_order(self, account_id, order):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append(("place", account_id, dict(order)))
        return {"ok": True}

    async def replace_order(self, account_id, order):
        self.calls.append(("replace", account_id, dict(order)))
        return {"ok": True}

    async def cancel_order(self, account_id, client_order_id):
        self.calls.append(("cancel", account_id, client_order_id))
        return {"ok": True}

    async def get_order_detail(self, account_id, client_order_id):
        return None


def order_payload(**changes):
    payload = {
        "accountRef": "account-ref", "symbol": "AAPL", "side": "BUY",
        "orderType": "LIMIT", "quantity": 1, "limitPrice": "100",
    }
    payload.update(changes)
    return payload


def test_control_plane_hides_broker_id_and_allows_cancel_when_trade_is_off():
    controls, _ = make_controls(global_enabled=False)
    principal = Principal("user-1", "device-1")
    with pytest.raises(AccessDenied, match="总开关"):
        controls.resolve(principal, "account-ref", mutation="submit")
    account = controls.resolve(principal, "account-ref", mutation="cancel")
    assert account.broker_account_id == "REAL-ACCOUNT-123"
    assert "REAL-ACCOUNT-123" not in str(controls.public_accounts(principal))


def test_cross_user_or_disabled_device_is_rejected():
    controls, _ = make_controls()
    with pytest.raises(AccessDenied):
        controls.resolve(Principal("other", "device-1"), "account-ref", mutation="read")
    with pytest.raises(AccessDenied):
        controls.resolve(Principal("user-1", "unknown"), "account-ref", mutation="read")


def test_device_signature_and_nonce_replay_protection():
    controls, private = make_controls()
    verifier = RequestAuthenticator(StaticTokenVerifier({"token": "user-1"}), controls)
    timestamp = str(time.time())
    nonce = "n" * 24
    body = b'{"accountRef":"account-ref"}'
    message = canonical_request("POST", "/v1/orders", body, timestamp, nonce, "idem")
    signature = private.sign(message, ec.ECDSA(hashes.SHA256()))
    encoded = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    proof = RequestProof("token", "device-1", timestamp, nonce, encoded, "idem")
    assert verifier.authenticate(proof, "POST", "/v1/orders", body) == Principal("user-1", "device-1")
    with pytest.raises(AccessDenied, match="重放"):
        verifier.authenticate(proof, "POST", "/v1/orders", body)


@pytest.mark.asyncio
async def test_submit_is_idempotent_and_never_accepts_real_account_id():
    controls, _ = make_controls()
    gateway = FakeInstitutionalGateway()
    service = InstitutionalTradingService(controls, MemoryOrderStore(), gateway)
    principal = Principal("user-1", "device-1")
    request_id = str(uuid.uuid4())
    first = await service.submit(principal, order_payload(), request_id)
    second = await service.submit(principal, order_payload(), request_id)
    assert first["order"]["clientOrderId"] == second["order"]["clientOrderId"]
    assert len(gateway.calls) == 1
    with pytest.raises(ValueError, match="禁止字段"):
        await service.submit(principal, order_payload(accountId="REAL-ACCOUNT-123"), str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_same_account_mutations_are_serialized():
    controls, _ = make_controls()
    gateway = FakeInstitutionalGateway()
    gateway.delay = 0.03
    limits = RiskLimits(max_open_orders=10)
    service = InstitutionalTradingService(controls, MemoryOrderStore(), gateway, limits=limits)
    principal = Principal("user-1", "device-1")
    await asyncio.gather(
        service.submit(principal, order_payload(limitPrice="100"), str(uuid.uuid4())),
        service.submit(principal, order_payload(limitPrice="101"), str(uuid.uuid4())),
    )
    assert [call[0] for call in gateway.calls] == ["place", "place"]


@pytest.mark.asyncio
async def test_replace_only_changes_limit_and_cancel_survives_trade_shutdown():
    controls, _ = make_controls()
    gateway = FakeInstitutionalGateway()
    store = MemoryOrderStore()
    service = InstitutionalTradingService(controls, store, gateway, limits=RiskLimits(max_open_orders=10))
    principal = Principal("user-1", "device-1")
    placed = await service.submit(principal, order_payload(), str(uuid.uuid4()))
    client_id = placed["order"]["clientOrderId"]
    replaced = await service.replace_limit(principal, "account-ref", client_id, "99.50", str(uuid.uuid4()))
    assert replaced["order"]["limitPrice"] == "99.50"

    off, _ = make_controls(global_enabled=False)
    controls.replace(ControlSnapshot.frozen(
        global_trade_enabled=False, version=2, users=off.snapshot.users,
        devices=off.snapshot.devices, accounts=off.snapshot.accounts,
    ))
    cancelled = await service.cancel(principal, "account-ref", client_id, str(uuid.uuid4()))
    assert cancelled["order"]["status"] == "CANCEL_REQUESTED"
    with pytest.raises(AccessDenied):
        await service.submit(principal, order_payload(), str(uuid.uuid4()))

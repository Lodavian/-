from __future__ import annotations

import argparse
import base64
import sys
import statistics
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from fast_order_server.controls import ControlPlane, ControlSnapshot
from fast_order_server.models import AccountGrant, DeviceGrant, UserGrant
from fast_order_server.security import RequestAuthenticator, RequestProof, StaticTokenVerifier, canonical_request


def percentile(values: list[float], fraction: float) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * fraction))]


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark FastOrder local auth and control snapshot hot path")
    parser.add_argument("--iterations", type=int, default=10_000)
    parser.add_argument("--p99-limit-ms", type=float, default=10.0)
    args = parser.parse_args()

    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")
    snapshot = ControlSnapshot.frozen(
        global_trade_enabled=True,
        version=1,
        users={"user": UserGrant("user", True, True)},
        devices={"device": DeviceGrant("device", "user", public, True)},
        accounts={"account": AccountGrant("account", "user", "never-logged", "bench", "••••0000", True, True)},
    )
    controls = ControlPlane(snapshot)
    verifier = RequestAuthenticator(StaticTokenVerifier({"token": "user"}), controls)
    body = b'{"accountRef":"account"}'
    prepared: list[RequestProof] = []
    for _ in range(args.iterations):
        timestamp = str(time.time())
        nonce = uuid.uuid4().hex
        idem = str(uuid.uuid4())
        signature = private.sign(canonical_request("POST", "/v1/orders", body, timestamp, nonce, idem), ec.ECDSA(hashes.SHA256()))
        prepared.append(RequestProof("token", "device", timestamp, nonce, base64.urlsafe_b64encode(signature).rstrip(b"=").decode(), idem))

    samples: list[float] = []
    for proof in prepared:
        started = time.perf_counter_ns()
        principal = verifier.authenticate(proof, "POST", "/v1/orders", body)
        controls.resolve(principal, "account", mutation="submit")
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    p99 = percentile(samples, 0.99)
    print(f"iterations={len(samples)} p50={statistics.median(samples):.3f}ms p95={percentile(samples, .95):.3f}ms p99={p99:.3f}ms max={max(samples):.3f}ms")
    return 0 if p99 <= args.p99_limit_ms else 1


if __name__ == "__main__":
    raise SystemExit(main())

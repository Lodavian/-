from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .controls import AccessDenied, ControlPlane
from .models import Principal


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Mapping[str, Any]: ...


class StaticTokenVerifier:
    """Test/local verifier. Production startup refuses to enable it."""

    def __init__(self, tokens: Mapping[str, str]):
        self._tokens = dict(tokens)

    def verify(self, token: str) -> Mapping[str, Any]:
        user_id = self._tokens.get(token)
        if not user_id:
            raise AccessDenied("登录令牌无效")
        return {"sub": user_id}


class CasdoorJwtVerifier:
    """Cached-JWKS verifier; normal requests perform no IAM network call."""

    def __init__(self, jwks_url: str, issuer: str, audience: str, *, refresh_seconds: int = 900):
        self.jwks_url = jwks_url
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.refresh_seconds = max(60, refresh_seconds)
        self._keys: dict[str, Any] = {}
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def verify(self, token: str) -> Mapping[str, Any]:
        try:
            import jwt
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("生产JWT验证依赖未安装") from exc
        header = jwt.get_unverified_header(token)
        kid = str(header.get("kid") or "")
        key = self._keys.get(kid)
        if not kid or key is None:
            # IAM is deliberately excluded from the request hot path. A newly
            # rotated key becomes usable after the background refresh.
            raise AccessDenied("身份系统签名密钥尚未同步")
        try:
            return jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub"]},
                leeway=5,
            )
        except Exception as exc:
            raise AccessDenied("登录令牌无效或已过期") from exc

    def refresh(self) -> None:
        try:
            import jwt
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("生产JWT验证依赖未安装") from exc
        with self._lock:
            request = urllib.request.Request(self.jwks_url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read(256 * 1024))
            keys: dict[str, Any] = {}
            for item in payload.get("keys", []) if isinstance(payload, dict) else []:
                if not isinstance(item, dict) or not item.get("kid"):
                    continue
                keys[str(item["kid"])] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(item))
            if not keys:
                raise AccessDenied("身份系统签名密钥不可用")
            self._keys = keys
            self._loaded_at = time.monotonic()


class NonceCache:
    def __init__(self, ttl_seconds: float = 120.0, max_entries: int = 100_000):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._values: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def consume(self, device_id: str, nonce: str, now: float | None = None) -> None:
        current = now if now is not None else time.time()
        key = (device_id, nonce)
        with self._lock:
            if len(self._values) >= self.max_entries:
                cutoff = current - self.ttl_seconds
                self._values = {item: seen for item, seen in self._values.items() if seen >= cutoff}
            previous = self._values.get(key)
            if previous is not None and current - previous <= self.ttl_seconds:
                raise AccessDenied("请求已被重放")
            self._values[key] = current


def canonical_request(
    method: str,
    path: str,
    body: bytes,
    timestamp: str,
    nonce: str,
    idempotency_key: str,
) -> bytes:
    body_hash = hashlib.sha256(body).hexdigest()
    return "\n".join(
        (method.upper(), path, body_hash, timestamp, nonce, idempotency_key)
    ).encode("utf-8")


def _b64url_decode(value: str) -> bytes:
    raw = value.encode("ascii")
    return base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))


@dataclass(slots=True)
class RequestProof:
    token: str
    device_id: str
    timestamp: str
    nonce: str
    signature: str
    idempotency_key: str = ""


class RequestAuthenticator:
    def __init__(
        self,
        tokens: TokenVerifier,
        controls: ControlPlane,
        *,
        allowed_skew_seconds: float = 30.0,
        nonce_cache: NonceCache | None = None,
        subject_claim: str = "sub",
    ):
        self.tokens = tokens
        self.controls = controls
        self.allowed_skew_seconds = allowed_skew_seconds
        self.nonces = nonce_cache or NonceCache()
        self.subject_claim = subject_claim

    def authenticate(self, proof: RequestProof, method: str, path: str, body: bytes) -> Principal:
        claims = self.tokens.verify(proof.token)
        user_id = str(claims.get(self.subject_claim) or "")
        if not user_id:
            raise AccessDenied("登录令牌缺少用户标识")
        try:
            sent_at = float(proof.timestamp)
        except (TypeError, ValueError):
            raise AccessDenied("请求时间戳无效") from None
        now = time.time()
        if abs(now - sent_at) > self.allowed_skew_seconds:
            raise AccessDenied("请求已过期")
        if not (16 <= len(proof.nonce) <= 128):
            raise AccessDenied("请求nonce无效")
        snapshot = self.controls.snapshot
        device = snapshot.devices.get(proof.device_id)
        if device is None or device.user_id != user_id or not device.enabled:
            raise AccessDenied("设备未获授权")
        message = canonical_request(
            method, path, body, proof.timestamp, proof.nonce, proof.idempotency_key
        )
        try:
            public_key = serialization.load_pem_public_key(device.public_key_pem.encode("ascii"))
            if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(public_key.curve, ec.SECP256R1):
                raise AccessDenied("设备密钥类型无效")
            public_key.verify(_b64url_decode(proof.signature), message, ec.ECDSA(hashes.SHA256()))
        except InvalidSignature as exc:
            raise AccessDenied("设备请求签名无效") from exc
        except AccessDenied:
            raise
        except Exception as exc:
            raise AccessDenied("设备请求签名无效") from exc
        self.nonces.consume(proof.device_id, proof.nonce, now)
        return Principal(user_id=user_id, device_id=proof.device_id)

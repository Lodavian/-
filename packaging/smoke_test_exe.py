"""Smoke-test the actual standalone FastOrder.exe without Python imports from it."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from pathlib import Path


CREDENTIAL_SERVICE = "极速下单.WebullOpenAPI"
CREDENTIAL_FIELDS = ("app_key", "app_secret", "account_id")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def fetch(path: str, *, expected: set[int] = {200}, timeout: float = 2.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8765{path}", timeout=timeout) as response:
            status, body = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read()
    if status not in expected:
        raise AssertionError(f"{path} returned {status}, expected {sorted(expected)}")
    return status, body


def wait_until_live(process: subprocess.Popen[bytes], deadline: float) -> None:
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read().decode("utf-8", "replace") if process.stdout else ""
            raise AssertionError(f"FastOrder exited before /livez was ready: {output}")
        try:
            fetch("/livez", timeout=0.3)
            return
        except (OSError, urllib.error.URLError, AssertionError):
            time.sleep(0.1)
    raise AssertionError("FastOrder /livez did not become ready")


def _receive_websocket_json(connection: socket.socket, initial: bytes) -> dict[str, object]:
    buffer = bytearray(initial)

    def require(size: int) -> None:
        while len(buffer) < size:
            chunk = connection.recv(4096)
            if not chunk:
                raise AssertionError("WebSocket closed before initial state arrived")
            buffer.extend(chunk)

    require(2)
    first, second = buffer[0], buffer[1]
    if first & 0x0F != 1 or second & 0x80:
        raise AssertionError("Unexpected WebSocket initial frame")
    cursor = 2
    length = second & 0x7F
    if length == 126:
        require(cursor + 2)
        length = int.from_bytes(buffer[cursor : cursor + 2], "big")
        cursor += 2
    elif length == 127:
        require(cursor + 8)
        length = int.from_bytes(buffer[cursor : cursor + 8], "big")
        cursor += 8
    require(cursor + length)
    value = json.loads(bytes(buffer[cursor : cursor + length]).decode("utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("WebSocket initial state is not a JSON object")
    return value


def websocket_handshake(token: str) -> dict[str, object]:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "GET /ws HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n"
        "Origin: http://127.0.0.1:8765\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Protocol: fast-order-session.{token}\r\n\r\n"
    ).encode("ascii")
    with socket.create_connection(("127.0.0.1", 8765), timeout=3) as connection:
        connection.sendall(request)
        response = bytearray()
        while b"\r\n\r\n" not in response:
            response.extend(connection.recv(4096))
        headers, frame = bytes(response).split(b"\r\n\r\n", 1)
        if not headers.startswith(b"HTTP/1.1 101"):
            raise AssertionError(f"WebSocket handshake failed: {headers[:100]!r}")
        return _receive_websocket_json(connection, frame)


def rejected_websocket_handshake(token: str, *, origin: str) -> None:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "GET /ws HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n"
        f"Origin: {origin}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Protocol: fast-order-session.{token}\r\n\r\n"
    ).encode("ascii")
    with socket.create_connection(("127.0.0.1", 8765), timeout=3) as connection:
        connection.sendall(request)
        response = connection.recv(1024)
    if response.startswith(b"HTTP/1.1 101"):
        raise AssertionError("Invalid WebSocket authentication was accepted")


def seed_test_credentials(
    local_app_data: Path, *, allow_unavailable: bool
) -> tuple[object | None, str | None]:
    import keyring

    backend = keyring.get_keyring()
    backend_name = f"{backend.__class__.__module__}.{backend.__class__.__name__}".lower()
    if "keyring.backends.windows" not in backend_name and "winvault" not in backend_name:
        raise AssertionError(f"Windows Credential Manager backend unavailable: {backend_name}")
    profile_id = uuid.uuid4().hex
    runtime = local_app_data / "FastOrder" / "runtime"
    runtime.mkdir(parents=True)
    profile = {
        "profile_id": profile_id,
        "account_mask": "••••0001",
        "environment": "LIVE",
        "auto_connect": False,
    }
    (runtime / "account_profile.json").write_text(
        json.dumps(profile, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    values = {
        "app_key": f"packaged-smoke-key-{profile_id}",
        "app_secret": f"packaged-smoke-secret-{profile_id}",
        "account_id": f"packaged-smoke-account-{profile_id}",
    }
    written: list[str] = []
    try:
        for field, value in values.items():
            keyring.set_password(CREDENTIAL_SERVICE, f"{profile_id}:{field}", value)
            written.append(field)
    except Exception as exc:
        for field in written:
            try:
                keyring.delete_password(CREDENTIAL_SERVICE, f"{profile_id}:{field}")
            except Exception:
                pass
        (runtime / "account_profile.json").unlink(missing_ok=True)
        if allow_unavailable:
            print(f"Credential Manager smoke skipped in this host session: {type(exc).__name__}")
            return None, None
        raise AssertionError("Windows Credential Manager smoke could not be executed") from exc
    return keyring, profile_id


def delete_test_credentials(keyring_module: object | None, profile_id: str | None) -> None:
    if keyring_module is None or profile_id is None:
        return
    for field in CREDENTIAL_FIELDS:
        try:
            keyring_module.delete_password(CREDENTIAL_SERVICE, f"{profile_id}:{field}")  # type: ignore[attr-defined]
        except Exception:
            pass


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        raise AssertionError("FastOrder did not exit gracefully") from exc
    if process.returncode != 0:
        raise AssertionError(f"FastOrder graceful exit returned {process.returncode}")


def launch(executable: Path, local_app_data: Path) -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    environment["LOCALAPPDATA"] = str(local_app_data)
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(
        [str(executable), "--no-browser"],
        cwd=str(executable.parent),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=flags,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--ui-manifest", type=Path, required=True)
    parser.add_argument("--allow-credential-manager-unavailable", action="store_true")
    args = parser.parse_args()
    executable = args.exe.resolve()
    dist = executable.parent
    expected_ui = json.loads(args.ui_manifest.read_text(encoding="utf-8"))["files"]
    before = tree_hashes(dist)

    with tempfile.TemporaryDirectory(prefix="FastOrder Packaged QA ") as temporary:
        local_app_data = Path(temporary) / "含空格和中文"
        keyring_module, profile_id = seed_test_credentials(
            local_app_data,
            allow_unavailable=args.allow_credential_manager_unavailable,
        )
        process = launch(executable, local_app_data)
        try:
            wait_until_live(process, time.monotonic() + 20)
            _, html = fetch("/")
            fetch("/readyz", expected={503})
            match = re.search(rb'name="fast-order-session-token" content="([^"]+)"', html)
            if not match:
                raise AssertionError("UI session token was not rendered")
            token = match.group(1).decode("ascii")
            initial = websocket_handshake(token)
            if keyring_module is not None:
                connection_state = initial.get("connection")
                if not isinstance(connection_state, dict) or connection_state.get("credentialState") != "CREDENTIALS_SAVED":
                    raise AssertionError("standalone EXE did not read the test Windows credential")
            rejected_websocket_handshake("invalid-token", origin="http://127.0.0.1:8765")
            rejected_websocket_handshake(token, origin="https://attacker.invalid")
            for relative, wanted in expected_ui.items():
                packaged_file = dist / Path(relative)
                if sha256(packaged_file) != str(wanted).upper():
                    raise AssertionError(f"packaged UI hash mismatch: {relative}")
                if not relative.endswith("index.html"):
                    _, body = fetch("/" + Path(relative).name)
                    actual = hashlib.sha256(body).hexdigest().upper()
                    if actual != str(wanted).upper():
                        raise AssertionError(f"served UI hash mismatch: {relative}")

            second = launch(executable, local_app_data)
            second.wait(timeout=8)
            if second.returncode != 0 or process.poll() is not None:
                raise AssertionError("second launch did not reuse the healthy instance")
        finally:
            try:
                stop_process(process)
            finally:
                delete_test_credentials(keyring_module, profile_id)

        data_root = local_app_data / "FastOrder"
        database = data_root / "runtime" / "orders.db"
        log_file = data_root / "logs" / "fast_order.log"
        if not database.is_file() or not log_file.is_file():
            raise AssertionError("LocalAppData database/log was not created")
        with closing(sqlite3.connect(database)) as connection:
            if str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                raise AssertionError("SQLite ledger is not using WAL")
            if int(connection.execute("PRAGMA synchronous").fetchone()[0]) != 2:
                raise AssertionError("SQLite ledger is not using FULL synchronous mode")
            connection.execute("CREATE TABLE IF NOT EXISTS packaged_smoke (value TEXT NOT NULL)")
            connection.execute("DELETE FROM packaged_smoke")
            connection.execute("INSERT INTO packaged_smoke(value) VALUES ('preserved')")
            connection.commit()

        restarted = launch(executable, local_app_data)
        try:
            wait_until_live(restarted, time.monotonic() + 20)
        finally:
            stop_process(restarted)
        with closing(sqlite3.connect(database)) as connection:
            value = connection.execute("SELECT value FROM packaged_smoke").fetchone()
        if value != ("preserved",):
            raise AssertionError("SQLite state did not survive restart")

        blocker = socket.socket()
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", 8765))
        blocker.listen(1)
        try:
            conflict = launch(executable, local_app_data)
            conflict.wait(timeout=8)
            if conflict.returncode == 0:
                raise AssertionError("foreign port occupation was not rejected")
        finally:
            blocker.close()

    if tree_hashes(dist) != before:
        raise AssertionError("standalone installation tree changed while running")
    print("FastOrder packaged executable smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

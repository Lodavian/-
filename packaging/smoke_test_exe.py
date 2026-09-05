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
from pathlib import Path


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


def websocket_handshake(token: str) -> None:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "GET /ws HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n"
        "Origin: http://127.0.0.1:8765\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Protocol: fast-order-session.{token}\r\n\r\n"
    ).encode("ascii")
    with socket.create_connection(("127.0.0.1", 8765), timeout=3) as connection:
        connection.sendall(request)
        response = connection.recv(4096)
    if not response.startswith(b"HTTP/1.1 101"):
        raise AssertionError(f"WebSocket handshake failed: {response[:100]!r}")


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
    args = parser.parse_args()
    executable = args.exe.resolve()
    dist = executable.parent
    expected_ui = json.loads(args.ui_manifest.read_text(encoding="utf-8"))["files"]
    before = tree_hashes(dist)

    with tempfile.TemporaryDirectory(prefix="FastOrder Packaged QA ") as temporary:
        local_app_data = Path(temporary) / "含空格和中文"
        process = launch(executable, local_app_data)
        try:
            wait_until_live(process, time.monotonic() + 20)
            _, html = fetch("/")
            fetch("/readyz", expected={200, 503})
            match = re.search(rb'name="fast-order-session-token" content="([^"]+)"', html)
            if not match:
                raise AssertionError("UI session token was not rendered")
            websocket_handshake(match.group(1).decode("ascii"))
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
            stop_process(process)

        data_root = local_app_data / "FastOrder"
        database = data_root / "runtime" / "orders.db"
        log_file = data_root / "logs" / "fast_order.log"
        if not database.is_file() or not log_file.is_file():
            raise AssertionError("LocalAppData database/log was not created")
        with sqlite3.connect(database) as connection:
            if str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                raise AssertionError("SQLite ledger is not using WAL")
            if int(connection.execute("PRAGMA synchronous").fetchone()[0]) != 2:
                raise AssertionError("SQLite ledger is not using FULL synchronous mode")
            connection.execute("CREATE TABLE IF NOT EXISTS packaged_smoke (value TEXT NOT NULL)")
            connection.execute("DELETE FROM packaged_smoke")
            connection.execute("INSERT INTO packaged_smoke(value) VALUES ('preserved')")

        restarted = launch(executable, local_app_data)
        try:
            wait_until_live(restarted, time.monotonic() + 20)
        finally:
            stop_process(restarted)
        with sqlite3.connect(database) as connection:
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

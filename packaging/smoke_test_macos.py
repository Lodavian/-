"""No-credential, no-order smoke test for a packaged macOS application."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def wait_json(url: str, wanted_status: int, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                status = response.status
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            status = exc.code
            payload = json.loads(exc.read())
        except (OSError, ValueError):
            time.sleep(0.25)
            continue
        if status == wanted_status:
            return payload
        time.sleep(0.25)
    raise AssertionError(f"endpoint did not reach HTTP {wanted_status}: {url}")


def assert_private_tree(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        value = path.lstat()
        if stat.S_ISLNK(value.st_mode):
            raise AssertionError(f"runtime symlink found: {path}")
        expected = 0o700 if path.is_dir() else 0o600
        if stat.S_IMODE(value.st_mode) != expected:
            raise AssertionError(f"unsafe mode for {path}: {stat.S_IMODE(value.st_mode):04o}")
        if value.st_uid != os.getuid():
            raise AssertionError(f"wrong owner for {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    args = parser.parse_args()
    app = args.app.resolve()
    plist = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    launcher = app / "Contents" / "MacOS" / str(plist["CFBundleExecutable"])
    executable = launcher.with_suffix("") if launcher.suffix == ".sh" else launcher
    if not executable.is_file():
        raise AssertionError("packaged executable not found")

    test_home = Path(tempfile.mkdtemp(prefix="fastorder-macos-smoke-"))
    test_home.chmod(0o700)
    environment = os.environ.copy()
    environment["HOME"] = str(test_home)
    environment["CFFIXED_USER_HOME"] = str(test_home)
    process = subprocess.Popen(
        [str(executable), "--no-browser"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        live = wait_json("http://127.0.0.1:8765/livez", 200)
        ready = wait_json("http://127.0.0.1:8765/readyz", 503)
        if live.get("ok") is not True or ready.get("ok") is not False:
            raise AssertionError("unexpected liveness/readiness payload")
        second = subprocess.run(
            [str(executable), "--no-browser"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if second.returncode != 0 or "已在运行" not in (second.stdout + second.stderr):
            raise AssertionError("second instance did not exit through the existing-instance path")
        data_root = test_home / "Library" / "Application Support" / "FastOrder"
        assert_private_tree(data_root)
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
        output = process.stdout.read() if process.stdout else ""
        shutil.rmtree(test_home, ignore_errors=True)
    if process.returncode != 0:
        raise AssertionError(f"packaged app did not stop cleanly: {output[-2000:]}")
    print("FastOrder macOS smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

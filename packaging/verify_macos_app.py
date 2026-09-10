"""Fail-closed audit for the internal FastOrder macOS arm64 app bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import subprocess
from pathlib import Path


FORBIDDEN_NAMES = {
    ".env", "account_profile.json", "broker_tokens", "logs", "orders.db",
    "runtime", "webull_tokens", "webull_data_streaming_sdk.log",
}
REQUIRED_RESOURCES = (
    "static/index.html",
    "static/styles.css",
    "static/app.js",
    "config/production.json",
    "LICENSES/THIRD_PARTY_NOTICES.md",
)
REQUIRED_PACKAGE_DATA = (
    "webull/core/data/endpoints.json",
    "webull/core/data/retry_config.json",
    "grpc/_cython/_credentials/roots.pem",
    "certifi/cacert.pem",
    "tzdata/zoneinfo/America/New_York",
)
SECRET_PATTERNS = (
    re.compile(rb"(?i)(api[_-]?secret|access[_-]?token|refresh[_-]?token|private[_-]?key)\s*[:=]\s*[^\s<>{}]+"),
    re.compile(rb"(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"(?i)\bsk-[a-z0-9]{20,}\b"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--ui-manifest", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path, required=True)
    args = parser.parse_args()
    app = args.app.resolve()
    source = args.source_root.resolve()
    contents = app / "Contents"
    resources = contents / "Resources"
    errors: list[str] = []

    try:
        plist = plistlib.loads((contents / "Info.plist").read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        plist = {}
        errors.append(f"Info.plist 无效: {type(exc).__name__}")
    if plist.get("CFBundleShortVersionString") != "1.0.0":
        errors.append("bundle 产品版本不是 1.0.0")
    minimum = str(plist.get("LSMinimumSystemVersion") or "")
    if not minimum.startswith("15"):
        errors.append("bundle 最低系统版本不是 macOS 15")

    executable_name = str(plist.get("CFBundleExecutable") or "")
    launcher = contents / "MacOS" / executable_name
    binary = launcher.with_suffix("") if launcher.suffix == ".sh" else launcher
    if not launcher.is_file():
        errors.append("bundle 启动文件缺失")
    if not binary.is_file():
        errors.append("bundle arm64 主程序缺失")
    else:
        result = subprocess.run(
            ["/usr/bin/lipo", "-archs", str(binary)], capture_output=True, text=True, check=False
        )
        if result.returncode != 0 or result.stdout.strip().split() != ["arm64"]:
            errors.append(f"主程序不是纯 arm64: {result.stdout.strip() or result.stderr.strip()}")

    ui_manifest = json.loads(args.ui_manifest.read_text(encoding="utf-8"))
    for relative, expected in ui_manifest.get("files", {}).items():
        source_file = source / relative
        bundled_file = resources / relative
        if not source_file.is_file() or sha256(source_file) != str(expected).lower():
            errors.append(f"源 UI hash 不匹配: {relative}")
        if not bundled_file.is_file() or sha256(bundled_file) != str(expected).lower():
            errors.append(f"bundle UI hash 不匹配: {relative}")
    for relative in REQUIRED_RESOURCES:
        if not (resources / relative).is_file():
            errors.append(f"bundle 缺少资源: {relative}")
    for relative in REQUIRED_PACKAGE_DATA:
        if not (contents / "MacOS" / relative).is_file():
            errors.append(f"bundle 缺少运行时 package data: {relative}")

    source_markers = (str(source).encode(), str(source).encode("utf-16le"))
    manifest: dict[str, dict[str, object]] = {}
    for entry in app.rglob("*"):
        parts = {part.lower() for part in entry.relative_to(app).parts}
        if parts & FORBIDDEN_NAMES or any(
            part.startswith(".env.") or part.startswith("orders.db-") for part in parts
        ):
            errors.append(f"bundle 包含禁止路径: {entry.relative_to(app).as_posix()}")

    for path in sorted(item for item in app.rglob("*") if item.is_file()):
        relative = path.relative_to(app).as_posix()
        if path.suffix.lower() in {".py", ".pyc", ".pdb", ".c", ".o"}:
            errors.append(f"bundle 包含禁止文件: {relative}")
        payload = path.read_bytes()
        if any(marker and marker in payload for marker in source_markers):
            errors.append(f"bundle 泄漏构建机源码路径: {relative}")
        if path.suffix.lower() in {".json", ".md", ".txt", ".xml", ".plist", ".sh"}:
            if any(pattern.search(payload) for pattern in SECRET_PATTERNS):
                errors.append(f"bundle 疑似包含敏感值: {relative}")
        manifest[relative] = {"sha256": sha256(path), "bytes": path.stat().st_size}

    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_out.write_text(
        json.dumps({"algorithm": "sha256", "root": app.name, "files": manifest}, indent=2) + "\n",
        encoding="utf-8",
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("FastOrder macOS app audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

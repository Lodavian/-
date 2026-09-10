import json
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
_SPEC = importlib.util.spec_from_file_location("fastorder_packaging_audit", PACKAGING / "verify_build.py")
_AUDIT = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_AUDIT)
audit_artifact = _AUDIT.audit_artifact
audit_ui = _AUDIT.audit_ui


def test_checked_in_ui_hashes_match_source():
    assert audit_ui(ROOT, PACKAGING / "ui-hashes.json") == []


def test_artifact_audit_accepts_required_resources_and_writes_manifest(tmp_path):
    dist = tmp_path / "FastOrder.dist"
    required = [
        "static/index.html",
        "static/styles.css",
        "static/app.js",
        "config/production.json",
        "FastOrder.ico",
        "webull/core/data/endpoints.json",
        "webull/core/data/retry_config.json",
        "grpc/_cython/_credentials/roots.pem",
        "certifi/cacert.pem",
        "tzdata/zoneinfo/America/New_York",
    ]
    for relative in required:
        target = dist / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = ROOT / Path(relative)
        target.write_bytes(source.read_bytes() if source.is_file() else b"test")
    (dist / "FastOrder.exe").write_bytes(b"binary")
    manifest = tmp_path / "artifact-manifest.json"

    errors, _ = audit_artifact(dist, PACKAGING / "artifact-policy.json", manifest)

    assert errors == []
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["algorithm"] == "sha256"
    assert "static/app.js" in saved["files"]


def test_artifact_audit_rejects_runtime_secrets_and_non_whitelisted_files(tmp_path):
    dist = tmp_path / "FastOrder.dist"
    (dist / "static").mkdir(parents=True)
    (dist / "static" / "app.js").write_text("access_token = super-secret\n", encoding="utf-8")
    (dist / ".env").write_text("FAST_ORDER_API_SECRET=secret\n", encoding="utf-8")
    (dist / "unexpected.exe").write_bytes(b"binary")

    errors, _ = audit_artifact(dist, PACKAGING / "artifact-policy.json", None)

    assert any("禁止文件" in error for error in errors)
    assert any("疑似包含密钥" in error for error in errors)
    assert any("不在白名单" in error for error in errors)


def test_artifact_audit_rejects_developer_absolute_path_in_binary(tmp_path):
    dist = tmp_path / "FastOrder.dist"
    dist.mkdir()
    (dist / "FastOrder.exe").write_bytes(
        b"MZ" + str(ROOT.resolve()).encode("utf-8") + b"\x00compiled"
    )
    errors, _ = audit_artifact(dist, PACKAGING / "artifact-policy.json", None)
    assert any("开发机绝对路径" in error for error in errors)


def test_macos_build_contract_is_arm64_private_and_keeps_ui_frozen():
    build = (PACKAGING / "build_macos.sh").read_text(encoding="utf-8")
    assert "--macos-target-arch=arm64" in build
    assert "--macos-app-macos-min-version=15.0" in build
    assert "--macos-app-console-mode=force" in build
    assert "--include-module=keyring.backends.macOS" in build
    assert "codesign --force --deep --sign -" in build
    assert "runtime-requirements-macos.lock" in build
    assert "pywin32" not in (PACKAGING / "runtime-requirements-macos.lock").read_text(encoding="utf-8")
    assert audit_ui(ROOT, PACKAGING / "ui-hashes.json") == []

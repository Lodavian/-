from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from fast_order.config import load_production_settings, load_settings
from fast_order.paths import AppPaths, PathMigrationError, PathResolutionError
from fast_order import paths as paths_module


def _legacy_profile(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "profile_id": "profile-1",
                "account_mask": "••••1234",
                "environment": "SANDBOX",
                "auto_connect": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_app_paths_default_to_localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    paths = AppPaths.resolve(Path(__file__).parents[1])
    assert paths.data_root == (tmp_path / "Local" / "FastOrder").resolve()
    assert paths.orders_db == paths.data_root / "runtime" / "orders.db"
    assert paths.account_profile == paths.data_root / "runtime" / "account_profile.json"
    assert paths.broker_tokens == paths.data_root / "runtime" / "broker_tokens"
    assert paths.logs == paths.data_root / "logs"


def test_production_path_resolution_requires_localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(PathResolutionError):
        AppPaths.resolve(Path(__file__).parents[1], development=False)


def test_macos_production_paths_use_application_support_without_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(paths_module.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("FAST_ORDER_DATA_DIR", str(tmp_path / "forbidden-override"))

    resolved = AppPaths.resolve(tmp_path / "FastOrder.app" / "Contents" / "Resources", production=True)

    assert resolved.data_root == home / "Library" / "Application Support" / "FastOrder"
    assert not resolved.data_root.is_relative_to(resolved.resource_root)


def test_production_manifest_is_strict_and_env_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource = tmp_path / "resources"
    manifest = resource / "config"
    manifest.mkdir(parents=True)
    policy = {
        "schemaVersion": 1,
        "productVersion": "1.0.0",
        "environment": "SANDBOX",
        "liveAllowed": False,
        "defaultSymbol": "AAPL",
        "port": 8765,
        "maxOrderQty": 1000,
        "maxOrderValue": 25000,
        "maxMarketQty": 100,
        "maxMarketEstimatedValue": 5000,
        "maxDailyGrossNotional": 25000,
        "maxOpenOrders": 1,
        "quoteStaleSeconds": 8,
        "sdkConnectTimeout": 10,
        "sdkReadTimeout": 15,
        "sdkTotalTimeout": 20,
    }
    (manifest / "production.json").write_text(json.dumps(policy), encoding="utf-8")
    monkeypatch.setenv("FAST_ORDER_WEBULL_APP_SECRET", "must-be-ignored")
    paths = AppPaths.resolve(resource, tmp_path / "data", development=False)
    settings = load_production_settings(paths)
    assert settings.default_symbol == "AAPL"
    assert settings.sandbox is True
    assert settings.app_key == ""
    assert settings.app_secret == ""
    raw = json.loads((manifest / "production.json").read_text(encoding="utf-8"))
    raw["unexpected"] = True
    (manifest / "production.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="字段"):
        load_production_settings(paths)


def test_production_paths_cannot_overlap_install_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "install" / "writable"))
    with pytest.raises(PathResolutionError, match="分离"):
        AppPaths.resolve(tmp_path / "install", production=True)


def test_legacy_migration_uses_sqlite_backup_and_marker(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    target = tmp_path / "user-data"
    (legacy / "runtime" / "webull_tokens").mkdir(parents=True)
    (legacy / "logs").mkdir()
    with sqlite3.connect(legacy / "runtime" / "orders.db") as database:
        database.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, value TEXT)")
        database.execute("INSERT INTO orders(value) VALUES ('preserved')")
    _legacy_profile(legacy / "runtime" / "account_profile.json")
    (legacy / "runtime" / "webull_tokens" / "token.json").write_text("token", encoding="utf-8")
    (legacy / "logs" / "fast_order.log").write_text("old log", encoding="utf-8")
    (legacy / "logs" / "webull_data_streaming_sdk.log").write_text("sdk log", encoding="utf-8")

    paths = AppPaths.resolve(legacy, target)
    report = paths.migrate_legacy()

    assert report["migrated"] is True
    with sqlite3.connect(paths.orders_db) as database:
        assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert database.execute("SELECT value FROM orders").fetchone()[0] == "preserved"
    assert json.loads(paths.account_profile.read_text(encoding="utf-8"))["account_mask"] == "••••1234"
    assert (paths.broker_tokens / "token.json").read_text(encoding="utf-8") == "token"
    imported_logs = list(paths.logs.glob("legacy-import-*/fast_order.log"))
    assert len(imported_logs) == 1
    assert imported_logs[0].read_text(encoding="utf-8") == "old log"
    assert not list(paths.logs.glob("legacy-import-*/webull_data_streaming_sdk.log"))
    marker = paths.migration_marker.read_text(encoding="utf-8")
    assert '"version": 1' in marker
    assert paths.migrate_legacy()["reason"] == "marker"


def test_legacy_log_import_redacts_sensitive_values(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    target = tmp_path / "target"
    (legacy / "logs").mkdir(parents=True)
    (legacy / "logs" / "fast_order.log").write_text(
        "account_id=VPH123456789 app_secret=secret-value Authorization: Bearer abc.def\n",
        encoding="utf-8",
    )
    paths = AppPaths.resolve(legacy, target)
    paths.migrate_legacy()
    imported = next(paths.logs.glob("legacy-import-*/fast_order.log")).read_text(encoding="utf-8")
    assert "VPH123456789" not in imported
    assert "secret-value" not in imported
    assert "abc.def" not in imported
    assert imported.count("[REDACTED]") >= 3


def test_invalid_profile_fails_closed_without_marker(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    target = tmp_path / "user-data"
    (legacy / "runtime").mkdir(parents=True)
    (legacy / "runtime" / "account_profile.json").write_text(
        json.dumps({"account_id": "must-not-migrate"}), encoding="utf-8"
    )
    paths = AppPaths.resolve(legacy, target)
    with pytest.raises(PathMigrationError):
        paths.migrate_legacy()
    assert not paths.migration_marker.exists()


def test_load_settings_exposes_paths_and_isolates_non_source_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAST_ORDER_DATA_DIR", raising=False)
    monkeypatch.delenv("FAST_ORDER_WEBULL_APP_KEY", raising=False)
    monkeypatch.delenv("FAST_ORDER_WEBULL_APP_SECRET", raising=False)
    settings = load_settings(tmp_path)
    assert settings.project_root == tmp_path.resolve()
    assert settings.paths is not None
    assert settings.app_paths.data_root == tmp_path.resolve()
    assert settings.token_dir == tmp_path / "runtime" / "broker_tokens"

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PathMigrationError(RuntimeError):
    """Raised when legacy runtime data cannot be migrated safely."""


class PathResolutionError(RuntimeError):
    """Raised when a production data root cannot be resolved safely."""


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Resolved read-only resources and per-user writable application data."""

    resource_root: Path
    data_root: Path
    runtime_root: Path
    orders_db: Path
    account_profile: Path
    broker_tokens: Path
    logs: Path
    migration_marker: Path

    @classmethod
    def resolve(
        cls,
        resource_root: Path,
        data_root: Path | None = None,
        *,
        development: bool | None = None,
        production: bool | None = None,
    ) -> "AppPaths":
        if production is not None:
            if development is not None and development == production:
                raise PathResolutionError("development 与 production 参数冲突")
            development = not production
        resource = Path(resource_root).resolve()
        if data_root is None:
            # A production process is deliberately not redirected by a
            # developer-only data-root override from its parent environment.
            configured = "" if production else os.environ.get("FAST_ORDER_DATA_DIR", "").strip()
            if configured:
                data = Path(configured).expanduser().resolve()
            elif development is True or (development is None and resource != _source_root()):
                # Explicit non-source roots are test/development roots. This
                # keeps existing Settings(project_root=tmp_path) fixtures
                # isolated while the real source entry point uses LOCALAPPDATA.
                data = resource
            else:
                local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
                if not local_app_data:
                    raise PathResolutionError("Windows LOCALAPPDATA 未配置，已停止启动")
                data = (Path(local_app_data) / "FastOrder").resolve()
        else:
            data = Path(data_root).expanduser().resolve()
        if production and (
            data == resource
            or data.is_relative_to(resource)
            or resource.is_relative_to(data)
        ):
            raise PathResolutionError("安装资源目录与用户数据目录必须完全分离")
        runtime = data / "runtime"
        return cls(
            resource_root=resource,
            data_root=data,
            runtime_root=runtime,
            orders_db=runtime / "orders.db",
            account_profile=runtime / "account_profile.json",
            broker_tokens=runtime / "broker_tokens",
            logs=data / "logs",
            migration_marker=data / ".migration-v1.json",
        )

    @classmethod
    def for_test(cls, root: Path) -> "AppPaths":
        return cls.resolve(root, root, development=True)

    @property
    def profile_path(self) -> Path:
        return self.account_profile

    @property
    def token_dir(self) -> Path:
        return self.broker_tokens

    @property
    def logs_dir(self) -> Path:
        return self.logs

    @property
    def static_dir(self) -> Path:
        return self.resource_root / "static"

    @property
    def env_file(self) -> Path:
        return self.resource_root / ".env"

    def ensure_directories(self) -> None:
        for path, label in (
            (self.runtime_root, "运行目录"),
            (self.broker_tokens, "SDK令牌目录"),
            (self.logs, "日志目录"),
        ):
            if path.exists() and (path.is_symlink() or not path.is_dir()):
                raise PathMigrationError(f"{label}路径不安全，已停止启动")
            path.mkdir(parents=True, exist_ok=True)
            if path.is_symlink() or not path.is_dir():
                raise PathMigrationError(f"{label}创建失败，已停止启动")
        try:
            from .credentials import harden_windows_acl
        except ImportError as exc:
            raise PathMigrationError("运行目录权限保护不可用，已停止启动") from exc
        for path in (self.runtime_root, self.broker_tokens, self.logs):
            if not harden_windows_acl(path):
                raise PathMigrationError(f"运行目录权限保护失败: {path.name}")

    def migrate_legacy(self, legacy_root: Path | None = None) -> dict[str, Any]:
        """Migrate legacy project-local runtime files exactly once.

        The marker is written only after every selected operation succeeds. A
        malformed profile, failed SQLite integrity check, symlinked token tree,
        or failed copy therefore leaves startup fail-closed instead of silently
        running with a partial account/order state.
        """
        legacy = Path(legacy_root or self.resource_root).resolve()
        if legacy == self.data_root:
            self.ensure_directories()
            return {"migrated": False, "reason": "same-root"}
        if self.migration_marker.is_symlink():
            raise PathMigrationError("运行数据迁移标记路径不安全，已停止启动")
        if self.migration_marker.exists():
            self._validate_marker()
            self.ensure_directories()
            return {"migrated": False, "reason": "marker"}

        self.ensure_directories()
        source_runtime = legacy / "runtime"
        source_logs = legacy / "logs"
        if not source_runtime.exists() and not source_logs.exists():
            return {"migrated": False, "reason": "no-legacy-data"}
        migrated: list[str] = []
        try:
            source_db = source_runtime / "orders.db"
            if source_db.is_symlink() or source_db.exists():
                self._migrate_database(source_db, self.orders_db)
                migrated.append("orders.db")

            source_profile = source_runtime / "account_profile.json"
            if source_profile.is_symlink() or source_profile.exists():
                self._migrate_profile(source_profile, self.account_profile)
                migrated.append("account_profile.json")

            source_tokens = source_runtime / "webull_tokens"
            if not source_tokens.exists() and not source_tokens.is_symlink():
                source_tokens = source_runtime / "broker_tokens"
            if source_tokens.is_symlink() or source_tokens.exists():
                self._migrate_tokens(source_tokens, self.broker_tokens)
                migrated.append("broker_tokens")

            if source_logs.is_symlink() or source_logs.exists():
                self._migrate_logs(source_logs, self.logs)
                migrated.append("logs")

            marker = {
                "version": 1,
                "source": str(legacy),
                "migrated": migrated,
            }
            self._write_marker(marker)
        except Exception as exc:
            if isinstance(exc, PathMigrationError):
                raise
            raise PathMigrationError("旧运行数据迁移失败，已停止启动") from exc
        return {"migrated": bool(migrated), "items": migrated}

    def _validate_marker(self) -> None:
        try:
            value = json.loads(self.migration_marker.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or set(value) != {"version", "source", "migrated"}
                or value.get("version") != 1
                or not isinstance(value.get("source"), str)
                or not isinstance(value.get("migrated"), list)
                or not all(isinstance(item, str) for item in value["migrated"])
            ):
                raise ValueError("invalid version")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise PathMigrationError("运行数据迁移标记损坏，已停止启动") from exc

    def _write_marker(self, value: dict[str, Any]) -> None:
        temp = self.migration_marker.with_name(f".{self.migration_marker.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temp, self.migration_marker)
        except OSError as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise PathMigrationError("运行数据迁移标记写入失败") from exc

    @staticmethod
    def _migrate_database(source: Path, destination: Path) -> None:
        if source.is_symlink() or destination.is_symlink():
            raise PathMigrationError("订单数据库路径包含不安全的符号链接")
        AppPaths._check_sqlite_integrity(source)
        source = source.resolve()
        destination = destination.resolve()
        if destination.exists():
            AppPaths._check_sqlite_integrity(destination)
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            source_db = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
            target_db = sqlite3.connect(temporary)
            try:
                source_db.backup(target_db)
                target_db.commit()
                result = target_db.execute("PRAGMA integrity_check").fetchone()
                if not result or str(result[0]).lower() != "ok":
                    raise PathMigrationError("旧订单数据库完整性校验失败")
            finally:
                source_db.close()
                target_db.close()
            os.replace(temporary, destination)
        except sqlite3.Error as exc:
            raise PathMigrationError("旧订单数据库迁移失败") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _check_sqlite_integrity(path: Path) -> None:
        try:
            database = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
            try:
                result = database.execute("PRAGMA integrity_check").fetchone()
            finally:
                database.close()
        except sqlite3.Error as exc:
            raise PathMigrationError("目标订单数据库完整性校验失败") from exc
        if not result or str(result[0]).lower() != "ok":
            raise PathMigrationError("目标订单数据库完整性校验失败")

    @staticmethod
    def _read_profile(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            allowed = {"profile_id", "account_mask", "environment", "auto_connect"}
            if not isinstance(value, dict) or set(value) != allowed:
                raise ValueError("unexpected profile fields")
            if not str(value.get("profile_id") or ""):
                raise ValueError("missing profile id")
            if str(value.get("environment") or "") not in {"SANDBOX", "LIVE"}:
                raise ValueError("invalid environment")
            if not str(value.get("account_mask") or "").startswith("••••"):
                raise ValueError("invalid account mask")
            if not isinstance(value.get("auto_connect"), bool):
                raise ValueError("invalid auto_connect")
            return value
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise PathMigrationError("账户配置元数据校验失败") from exc

    @staticmethod
    def _migrate_profile(source: Path, destination: Path) -> None:
        if source.is_symlink() or destination.is_symlink():
            raise PathMigrationError("账户配置路径包含不安全的符号链接")
        value = AppPaths._read_profile(source)
        if destination.exists():
            if AppPaths._read_profile(destination) != value:
                raise PathMigrationError("目标账户配置与旧配置冲突")
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temp, destination)
            from .credentials import harden_windows_acl

            if not harden_windows_acl(destination.parent) or not harden_windows_acl(destination):
                raise PathMigrationError("账户配置权限保护失败")
        except OSError as exc:
            raise PathMigrationError("账户配置元数据迁移失败") from exc
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _iter_regular_files(root: Path):
        if root.is_symlink():
            raise PathMigrationError("令牌目录包含不安全的符号链接")
        for path in root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                if path.is_symlink():
                    raise PathMigrationError("令牌目录包含不安全的符号链接")
                continue
            yield path

    @staticmethod
    def _migrate_tokens(source: Path, destination: Path) -> None:
        files = list(AppPaths._iter_regular_files(source))
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging: Path | None = Path(tempfile.mkdtemp(prefix="fast-order-token-migration-", dir=str(destination.parent)))
        try:
            from .credentials import harden_windows_acl

            if not harden_windows_acl(staging):
                raise PathMigrationError("SDK令牌暂存目录权限保护失败")
            for path in files:
                relative = path.relative_to(source)
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            if not destination.exists():
                os.replace(staging, destination)
                staging = None
            else:
                if destination.is_symlink() or not destination.is_dir():
                    raise PathMigrationError("目标令牌目录不安全")
                for path in files:
                    relative = path.relative_to(source)
                    target = destination / relative
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(staging / relative, target)
        except (OSError, ValueError) as exc:
            raise PathMigrationError("SDK令牌迁移失败") from exc
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        try:
            if not harden_windows_acl(destination):
                raise PathMigrationError("SDK令牌目录权限保护失败")
        except ImportError as exc:
            raise PathMigrationError("SDK令牌目录权限保护不可用，已停止启动") from exc

    @staticmethod
    def _migrate_logs(source: Path, destination: Path) -> None:
        import_dir = destination / f"legacy-import-{uuid.uuid4().hex}"
        import_dir.mkdir(parents=True, exist_ok=True)
        try:
            for path in AppPaths._iter_regular_files(source):
                name = path.name.lower()
                # SDK logs can contain request/credential material and do not
                # belong in the application-owned log archive.
                if "sdk" in name or "webull_data_streaming" in name:
                    continue
                if path.suffix.lower() != ".log":
                    continue
                target = import_dir / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                sanitized = AppPaths._sanitize_log(path)
                if sanitized is None:
                    continue
                target.write_text(sanitized, encoding="utf-8")
            from .credentials import harden_windows_acl

            if not harden_windows_acl(destination) or not harden_windows_acl(import_dir):
                raise PathMigrationError("应用日志目录权限保护失败")
        except (OSError, ValueError) as exc:
            raise PathMigrationError("应用日志迁移失败") from exc

    @staticmethod
    def _sanitize_log(path: Path) -> str | None:
        """Return a UTF-8 text log with credential-like values redacted.

        Legacy logs are untrusted input.  Binary/undecodable files are skipped
        rather than copied verbatim, and sensitive key/value fields are
        replaced line-by-line before they reach the new mutable data root.
        """
        try:
            raw = path.read_bytes()
            if b"\x00" in raw:
                return None
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        patterns = (
            re.compile(r"(?i)([\"']?authorization[\"']?\s*[:=]\s*)([^\r\n]+)"),
            re.compile(
                r"(?i)([\"']?(?:app[_ -]?secret|access[_ -]?token|refresh[_ -]?token|signature|account[_ -]?id)[\"']?\s*[:=]\s*[\"']?)([^\s,;}\"']+)"
            ),
            re.compile(r"(?i)(\bbearer\s+)([^\s]+)"),
        )
        lines: list[str] = []
        for line in text.splitlines(keepends=True):
            for pattern in patterns:
                line = pattern.sub(r"\1[REDACTED]", line)
            lines.append(line)
        return "".join(lines)


def _source_root() -> Path:
    return Path(__file__).resolve().parent.parent

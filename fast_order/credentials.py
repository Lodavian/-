from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .paths import AppPaths


SERVICE_NAME = "极速下单.WebullOpenAPI"
PROFILE_FILENAME = "account_profile.json"
_CREDENTIAL_FIELDS = ("app_key", "app_secret", "account_id")


def harden_windows_acl(path: Path) -> bool:
    """Restrict a sensitive runtime path to this user and Windows administrators."""
    target = Path(path)
    if os.name != "nt" or not target.exists():
        return True
    try:
        identity = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=False,
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    sid_match = re.search(rb"S-\d(?:-\d+)+", identity.stdout or b"")
    if identity.returncode != 0 or sid_match is None:
        return False
    user_sid = sid_match.group(0).decode("ascii")
    inheritance = "(OI)(CI)F" if target.is_dir() else "F"
    try:
        result = subprocess.run(
            [
                "icacls", str(target), "/inheritance:r", "/grant:r",
                f"*{user_sid}:{inheritance}", "*S-1-5-18:F", "*S-1-5-32-544:F",
            ],
            capture_output=True,
            text=False,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


class CredentialStoreError(RuntimeError):
    """Base error for secure credential persistence."""


class CredentialStoreUnavailable(CredentialStoreError):
    """Raised when the operating system credential store cannot be used safely."""


class CredentialStoreCorrupt(CredentialStoreError):
    """Raised when saved metadata and secure credentials do not agree."""


class _KeyringBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


@dataclass(frozen=True, slots=True)
class AccountProfile:
    profile_id: str
    account_mask: str
    environment: str
    auto_connect: bool


@dataclass(frozen=True, slots=True)
class StoredCredentials:
    app_key: str
    app_secret: str
    account_id: str
    profile: AccountProfile


@dataclass(frozen=True, slots=True)
class ResolvedCredentials:
    app_key: str
    app_secret: str
    account_id: str
    source: str
    profile: AccountProfile | None = None


def mask_account_id(account_id: str) -> str:
    normalized = account_id.strip()
    if not normalized:
        return ""
    visible = normalized[-4:]
    return f"••••{visible}"


def _credential_username(profile_id: str, field: str) -> str:
    return f"{profile_id}:{field}"


def _secure_store_name() -> str:
    return "macOS 登录钥匙串" if sys.platform == "darwin" else "Windows Credential Manager"


class CredentialStore:
    """Persist Webull credentials only in the native system credential store.

    The adjacent JSON profile intentionally contains display-only metadata. There
    is no plaintext fallback: callers may still connect with in-memory values for
    the current process when this store is unavailable.
    """

    def __init__(
        self,
        project_root: Path | AppPaths,
        *,
        backend: _KeyringBackend | None = None,
        require_windows_backend: bool = True,
    ) -> None:
        self.paths = project_root if isinstance(project_root, AppPaths) else AppPaths.resolve(project_root, project_root, development=True)
        self.project_root = self.paths.data_root
        self.profile_path = self.paths.account_profile
        self._backend = backend
        self._require_windows_backend = require_windows_backend
        self._protect_metadata_acl = backend is None

    def _get_backend(self) -> _KeyringBackend:
        if self._backend is not None:
            return self._backend
        if sys.platform == "darwin":
            try:
                from keyring.backends.macOS import Keyring

                # Accessing priority verifies that the Security framework API loaded.
                _ = Keyring.priority
                backend = Keyring()
            except Exception as exc:  # pragma: no cover - depends on the macOS host
                raise CredentialStoreUnavailable("macOS 登录钥匙串当前不可用") from exc
            if (
                backend.__class__.__module__ != "keyring.backends.macOS"
                or backend.__class__.__name__ != "Keyring"
            ):
                raise CredentialStoreUnavailable("未检测到 macOS 登录钥匙串后端")
        elif os.name == "nt":
            try:
                import keyring

                backend = keyring.get_keyring()
            except Exception as exc:  # pragma: no cover - depends on host installation
                raise CredentialStoreUnavailable("Windows Credential Manager 当前不可用") from exc

            module_name = backend.__class__.__module__.lower()
            class_name = backend.__class__.__name__.lower()
            if self._require_windows_backend and not (
                "keyring.backends.windows" in module_name or "winvault" in class_name
            ):
                raise CredentialStoreUnavailable("未检测到 Windows Credential Manager 后端")
        else:
            raise CredentialStoreUnavailable("当前平台没有受支持的系统安全凭据存储")
        self._backend = backend
        return backend

    @property
    def available(self) -> bool:
        try:
            self._get_backend()
        except CredentialStoreUnavailable:
            return False
        return True

    def load_profile(self) -> AccountProfile | None:
        if not self.profile_path.exists() and not self.profile_path.is_symlink():
            return None
        if sys.platform == "darwin":
            from .platform_security import validate_macos_path

            try:
                validate_macos_path(self.profile_path, directory=False)
            except RuntimeError as exc:
                raise CredentialStoreCorrupt("账户配置元数据路径不安全") from exc
        try:
            raw = json.loads(self.profile_path.read_text(encoding="utf-8"))
            allowed = {"profile_id", "account_mask", "environment", "auto_connect"}
            if not isinstance(raw, dict) or set(raw) != allowed:
                raise ValueError("unexpected profile fields")
            if (
                not isinstance(raw["profile_id"], str)
                or not raw["profile_id"].strip()
                or not isinstance(raw["account_mask"], str)
                or not raw["account_mask"].startswith("••••")
                or raw["environment"] not in {"SANDBOX", "LIVE"}
                or type(raw["auto_connect"]) is not bool
            ):
                raise ValueError("invalid profile metadata")
            profile = AccountProfile(
                profile_id=str(raw["profile_id"]),
                account_mask=str(raw["account_mask"]),
                environment=str(raw["environment"]),
                auto_connect=raw["auto_connect"],
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError) as exc:
            raise CredentialStoreCorrupt("账户配置元数据损坏") from exc
        if not profile.profile_id or profile.environment not in {"SANDBOX", "LIVE"}:
            raise CredentialStoreCorrupt("账户配置元数据无效")
        if not profile.account_mask.startswith("••••"):
            raise CredentialStoreCorrupt("账户脱敏标识无效")
        return profile

    def load_credentials(self) -> StoredCredentials | None:
        profile = self.load_profile()
        if profile is None:
            return None
        backend = self._get_backend()
        values: dict[str, str] = {}
        try:
            for field in _CREDENTIAL_FIELDS:
                value = backend.get_password(
                    SERVICE_NAME, _credential_username(profile.profile_id, field)
                )
                if not value:
                    raise CredentialStoreCorrupt(f"{_secure_store_name()} 中的账户凭据不完整")
                values[field] = value
        except CredentialStoreCorrupt:
            raise
        except Exception as exc:
            raise CredentialStoreUnavailable(f"无法读取 {_secure_store_name()}") from exc
        return StoredCredentials(profile=profile, **values)

    def save_credentials(
        self,
        *,
        app_key: str,
        app_secret: str,
        account_id: str,
        sandbox: bool,
        auto_connect: bool,
    ) -> AccountProfile:
        app_key = app_key.strip()
        app_secret = app_secret.strip()
        account_id = account_id.strip()
        if not app_key or not app_secret or not account_id:
            raise ValueError("App Key、App Secret 和 Account ID 均不能为空")

        backend = self._get_backend()
        existing = self.load_profile()
        profile_id = existing.profile_id if existing else uuid.uuid4().hex
        profile = AccountProfile(
            profile_id=profile_id,
            account_mask=mask_account_id(account_id),
            environment="SANDBOX" if sandbox else "LIVE",
            auto_connect=bool(auto_connect),
        )
        values = {"app_key": app_key, "app_secret": app_secret, "account_id": account_id}
        written: list[str] = []
        try:
            for field, value in values.items():
                backend.set_password(SERVICE_NAME, _credential_username(profile_id, field), value)
                written.append(field)
        except Exception as exc:
            # Do not create plaintext metadata for a credential set that was not
            # successfully committed to the native secure store.
            for field in written:
                try:
                    backend.delete_password(SERVICE_NAME, _credential_username(profile_id, field))
                except Exception:
                    pass
            raise CredentialStoreUnavailable(f"无法写入 {_secure_store_name()}") from exc

        try:
            self._write_profile(profile)
        except CredentialStoreError:
            for field in _CREDENTIAL_FIELDS:
                try:
                    backend.delete_password(SERVICE_NAME, _credential_username(profile_id, field))
                except Exception:
                    pass
            raise
        return profile

    def delete_credentials(self) -> None:
        profile = self.load_profile()
        if profile is None:
            return
        backend = self._get_backend()
        failures: list[Exception] = []
        for field in _CREDENTIAL_FIELDS:
            try:
                backend.delete_password(
                    SERVICE_NAME, _credential_username(profile.profile_id, field)
                )
            except Exception as exc:
                if exc.__class__.__name__ != "PasswordDeleteError":
                    failures.append(exc)
        if failures:
            raise CredentialStoreUnavailable(f"无法完整删除 {_secure_store_name()} 凭据") from failures[0]
        try:
            self.profile_path.unlink(missing_ok=True)
        except OSError as exc:
            raise CredentialStoreError("凭据已删除，但账户元数据清理失败") from exc

    def _write_profile(self, profile: AccountProfile) -> None:
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.profile_path.with_suffix(".tmp")
        payload = json.dumps(asdict(profile), ensure_ascii=False, indent=2) + "\n"
        try:
            temp_path.write_text(payload, encoding="utf-8")
            os.replace(temp_path, self.profile_path)
            if self._protect_metadata_acl:
                from .platform_security import protect_runtime_path

                protected = protect_runtime_path(self.profile_path.parent) and protect_runtime_path(
                    self.profile_path
                )
            else:
                protected = True
            if not protected:
                self.profile_path.unlink(missing_ok=True)
                raise CredentialStoreError("账户元数据权限保护失败")
        except OSError as exc:
            temp_path.unlink(missing_ok=True)
            raise CredentialStoreError("账户元数据保存失败") from exc


def resolve_credentials(
    store: CredentialStore,
    *,
    env_app_key: str,
    env_app_secret: str,
    env_account_id: str,
) -> ResolvedCredentials | None:
    """Resolve saved UI credentials before the developer `.env` fallback."""

    profile = store.load_profile()
    if profile is not None:
        saved = store.load_credentials()
        if saved is None:  # defensive; profile existence guarantees a result
            raise CredentialStoreCorrupt("保存的账户凭据不可用")
        return ResolvedCredentials(
            app_key=saved.app_key,
            app_secret=saved.app_secret,
            account_id=saved.account_id,
            source="secure_store",
            profile=saved.profile,
        )

    app_key = env_app_key.strip()
    app_secret = env_app_secret.strip()
    account_id = env_account_id.strip()
    if app_key and app_secret:
        return ResolvedCredentials(
            app_key=app_key,
            app_secret=app_secret,
            account_id=account_id,
            source="env",
        )
    return None

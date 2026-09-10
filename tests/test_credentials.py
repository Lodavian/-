from __future__ import annotations

import json
import pytest

from fast_order.credentials import (
    SERVICE_NAME,
    CredentialStore,
    CredentialStoreCorrupt,
    CredentialStoreUnavailable,
    mask_account_id,
    resolve_credentials,
)
from fast_order import credentials as credentials_module


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class FailingKeyring(FakeKeyring):
    def set_password(self, service: str, username: str, password: str) -> None:
        raise RuntimeError("backend unavailable")


def test_save_keeps_secrets_out_of_profile_json(tmp_path) -> None:
    backend = FakeKeyring()
    store = CredentialStore(tmp_path, backend=backend)

    profile = store.save_credentials(
        app_key="app-key-value",
        app_secret="super-secret-value",
        account_id="1234567890",
        sandbox=True,
        auto_connect=True,
    )

    raw_text = store.profile_path.read_text(encoding="utf-8")
    raw = json.loads(raw_text)
    assert set(raw) == {"profile_id", "account_mask", "environment", "auto_connect"}
    assert raw["account_mask"] == "••••7890"
    assert raw["environment"] == "SANDBOX"
    assert raw["auto_connect"] is True
    assert "app-key-value" not in raw_text
    assert "super-secret-value" not in raw_text
    assert "1234567890" not in raw_text

    loaded = store.load_credentials()
    assert loaded is not None
    assert loaded.app_key == "app-key-value"
    assert loaded.app_secret == "super-secret-value"
    assert loaded.account_id == "1234567890"
    assert loaded.profile == profile
    assert all(service == SERVICE_NAME for service, _ in backend.values)


def test_secure_store_has_priority_over_env(tmp_path) -> None:
    store = CredentialStore(tmp_path, backend=FakeKeyring())
    store.save_credentials(
        app_key="stored-key",
        app_secret="stored-secret",
        account_id="stored-account",
        sandbox=False,
        auto_connect=False,
    )

    resolved = resolve_credentials(
        store,
        env_app_key="env-key",
        env_app_secret="env-secret",
        env_account_id="env-account",
    )

    assert resolved is not None
    assert resolved.source == "secure_store"
    assert resolved.app_key == "stored-key"
    assert resolved.app_secret == "stored-secret"
    assert resolved.account_id == "stored-account"


def test_env_is_used_only_when_no_saved_profile(tmp_path) -> None:
    store = CredentialStore(tmp_path, backend=FakeKeyring())
    resolved = resolve_credentials(
        store,
        env_app_key=" env-key ",
        env_app_secret=" env-secret ",
        env_account_id=" env-account ",
    )
    assert resolved is not None
    assert resolved.source == "env"
    assert resolved.app_key == "env-key"
    assert resolved.account_id == "env-account"


def test_missing_saved_secret_does_not_fall_back_to_env(tmp_path) -> None:
    backend = FakeKeyring()
    store = CredentialStore(tmp_path, backend=backend)
    profile = store.save_credentials(
        app_key="stored-key",
        app_secret="stored-secret",
        account_id="12345678",
        sandbox=True,
        auto_connect=False,
    )
    backend.values.pop((SERVICE_NAME, f"{profile.profile_id}:app_secret"))

    with pytest.raises(CredentialStoreCorrupt):
        resolve_credentials(
            store,
            env_app_key="env-key",
            env_app_secret="env-secret",
            env_account_id="env-account",
        )


def test_unavailable_keyring_never_writes_plaintext_metadata(tmp_path) -> None:
    store = CredentialStore(tmp_path, backend=FailingKeyring())
    with pytest.raises(CredentialStoreUnavailable):
        store.save_credentials(
            app_key="key",
            app_secret="secret",
            account_id="account",
            sandbox=True,
            auto_connect=False,
        )
    assert not store.profile_path.exists()
    assert list(tmp_path.rglob("*secret*")) == []


def test_macos_forces_native_keyring_backend(monkeypatch, tmp_path) -> None:
    import keyring.backends.macOS as macos_backend

    NativeKeyring = type(
        "Keyring",
        (FakeKeyring,),
        {"__module__": "keyring.backends.macOS", "priority": 5},
    )
    monkeypatch.setattr(credentials_module.sys, "platform", "darwin")
    monkeypatch.setattr(macos_backend, "Keyring", NativeKeyring)
    store = CredentialStore(tmp_path)

    assert store.available is True
    assert store._get_backend().__class__.__module__ == "keyring.backends.macOS"


def test_delete_removes_keyring_values_before_metadata(tmp_path) -> None:
    backend = FakeKeyring()
    store = CredentialStore(tmp_path, backend=backend)
    store.save_credentials(
        app_key="key",
        app_secret="secret",
        account_id="account",
        sandbox=True,
        auto_connect=False,
    )
    assert backend.values

    store.delete_credentials()

    assert backend.values == {}
    assert not store.profile_path.exists()


def test_profile_rejects_extra_or_sensitive_fields(tmp_path) -> None:
    store = CredentialStore(tmp_path, backend=FakeKeyring())
    store.profile_path.parent.mkdir(parents=True)
    store.profile_path.write_text(
        json.dumps(
            {
                "profile_id": "profile",
                "account_mask": "••••1234",
                "environment": "SANDBOX",
                "auto_connect": False,
                "app_secret": "must-not-be-here",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CredentialStoreCorrupt):
        store.load_profile()


@pytest.mark.parametrize(
    ("account_id", "expected"),
    [("123456789", "••••6789"), ("AB", "••••AB"), ("", "")],
)
def test_account_mask(account_id: str, expected: str) -> None:
    assert mask_account_id(account_id) == expected

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from fast_order import platform_security


def _stat(mode: int, uid: int = 501):
    return SimpleNamespace(st_mode=mode, st_uid=uid)


def test_macos_path_requires_exact_private_modes(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "runtime"
    monkeypatch.setattr(platform_security.sys, "platform", "darwin")
    monkeypatch.setattr(platform_security.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(Path, "lstat", lambda _self: _stat(stat.S_IFDIR | 0o700))
    platform_security.validate_macos_path(target, directory=True)

    monkeypatch.setattr(Path, "lstat", lambda _self: _stat(stat.S_IFDIR | 0o755))
    with pytest.raises(platform_security.PlatformSecurityError, match="权限不安全"):
        platform_security.validate_macos_path(target, directory=True)


def test_macos_path_rejects_symlink_and_wrong_owner(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "runtime"
    monkeypatch.setattr(platform_security.sys, "platform", "darwin")
    monkeypatch.setattr(platform_security.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(Path, "lstat", lambda _self: _stat(stat.S_IFLNK | 0o777))
    with pytest.raises(platform_security.PlatformSecurityError, match="符号链接"):
        platform_security.validate_macos_path(target, directory=True)

    monkeypatch.setattr(Path, "lstat", lambda _self: _stat(stat.S_IFREG | 0o600, uid=502))
    with pytest.raises(platform_security.PlatformSecurityError, match="不属于当前用户"):
        platform_security.validate_macos_path(target, directory=False)


def test_macos_tree_rejects_nested_symlink(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "FastOrder"
    token_link = root / "runtime" / "broker_tokens"
    monkeypatch.setattr(platform_security.sys, "platform", "darwin")
    monkeypatch.setattr(platform_security.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(
        platform_security.os,
        "walk",
        lambda *_args, **_kwargs: [(str(root), ["runtime"], []), (str(root / "runtime"), ["broker_tokens"], [])],
    )

    def fake_lstat(path: Path):
        if path == token_link:
            return _stat(stat.S_IFLNK | 0o777)
        return _stat(stat.S_IFDIR | 0o700)

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    with pytest.raises(platform_security.PlatformSecurityError, match="符号链接"):
        platform_security.validate_macos_tree(root)


def test_private_umask_is_macos_only(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(platform_security.sys, "platform", "darwin")
    monkeypatch.setattr(platform_security.os, "umask", lambda mode: calls.append(mode))
    platform_security.activate_private_umask()
    assert calls == [0o077]

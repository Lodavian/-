from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


DIRECTORY_MODE = 0o700
FILE_MODE = 0o600


class PlatformSecurityError(RuntimeError):
    """Raised when a platform-owned runtime path is not safe to use."""


def activate_private_umask() -> None:
    """Make every subsequently created macOS runtime object private by default."""

    if sys.platform == "darwin":
        os.umask(0o077)


def _validate_stat(path: Path, value: os.stat_result, *, directory: bool) -> None:
    expected_kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_kind(value.st_mode):
        label = "目录" if directory else "文件"
        raise PlatformSecurityError(f"用户数据{label}类型不安全: {path.name}")
    if value.st_uid != os.getuid():
        raise PlatformSecurityError(f"用户数据路径不属于当前用户: {path.name}")
    wanted = DIRECTORY_MODE if directory else FILE_MODE
    actual = stat.S_IMODE(value.st_mode)
    if actual != wanted:
        raise PlatformSecurityError(
            f"用户数据路径权限不安全: {path.name} ({actual:04o}，要求 {wanted:04o})"
        )


def validate_macos_path(path: Path, *, directory: bool) -> None:
    """Validate one existing macOS path without following a symbolic link."""

    if sys.platform != "darwin":
        return
    target = Path(path)
    try:
        value = target.lstat()
    except OSError as exc:
        raise PlatformSecurityError(f"无法检查用户数据路径: {target.name}") from exc
    if stat.S_ISLNK(value.st_mode):
        raise PlatformSecurityError(f"用户数据路径不能是符号链接: {target.name}")
    _validate_stat(target, value, directory=directory)


def ensure_macos_directory(path: Path) -> None:
    """Create a private application directory, or reject an unsafe existing one."""

    if sys.platform != "darwin":
        return
    target = Path(path)
    try:
        home = Path.home()
        cursor = target
        while True:
            if os.path.lexists(cursor) and cursor.is_symlink():
                raise PlatformSecurityError(f"用户数据路径包含符号链接: {cursor.name}")
            if cursor == home or cursor.parent == cursor:
                break
            cursor = cursor.parent
        if target.exists() or target.is_symlink():
            validate_macos_path(target, directory=True)
            return
        target.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=False)
        os.chmod(target, DIRECTORY_MODE, follow_symlinks=False)
        validate_macos_path(target, directory=True)
    except PlatformSecurityError:
        raise
    except OSError as exc:
        raise PlatformSecurityError(f"无法创建安全用户数据目录: {target.name}") from exc


def validate_macos_tree(root: Path) -> None:
    """Fail closed if any object in the app-owned data tree is unsafe."""

    if sys.platform != "darwin":
        return
    root = Path(root)
    validate_macos_path(root, directory=True)
    try:
        for current, directories, files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            for name in directories:
                validate_macos_path(current_path / name, directory=True)
            for name in files:
                validate_macos_path(current_path / name, directory=False)
    except PlatformSecurityError:
        raise
    except OSError as exc:
        raise PlatformSecurityError("无法检查用户数据目录") from exc


def protect_runtime_path(path: Path, *, recursive: bool = False) -> bool:
    """Apply the existing Windows ACL policy or verify strict macOS modes."""

    target = Path(path)
    if os.name == "nt":
        # Imported lazily to avoid the credentials/paths import cycle.
        from .credentials import harden_windows_acl

        return harden_windows_acl(target)
    if sys.platform != "darwin":
        return True
    try:
        if recursive and target.is_dir():
            validate_macos_tree(target)
        else:
            validate_macos_path(target, directory=target.is_dir())
    except PlatformSecurityError:
        return False
    return True

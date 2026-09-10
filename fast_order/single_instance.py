from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


MUTEX_NAME = r"Local\FastOrder.V1"
ERROR_ALREADY_EXISTS = 183


class SingleInstanceError(RuntimeError):
    """Raised when the platform single-instance primitive cannot be created."""


@dataclass(slots=True)
class SingleInstance:
    """Own the Windows mutex or macOS flock for the process lifetime."""

    handle: int | None
    already_running: bool
    kind: str = "inert"

    @classmethod
    def acquire(cls, lock_path: Path | None = None) -> "SingleInstance":
        if sys.platform == "darwin":
            if lock_path is None:
                raise SingleInstanceError("macOS 单实例锁路径未配置")
            return cls._acquire_macos(Path(lock_path))
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            create_mutex = kernel32.CreateMutexW
            create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
            create_mutex.restype = ctypes.c_void_p
            ctypes.set_last_error(0)
            handle = create_mutex(None, False, MUTEX_NAME)
            if not handle:
                raise SingleInstanceError(f"无法创建单实例锁（Windows 错误 {ctypes.get_last_error()}）")
            return cls(
                handle=int(handle),
                already_running=ctypes.get_last_error() == ERROR_ALREADY_EXISTS,
                kind="windows",
            )
        return cls(handle=None, already_running=False)

    @classmethod
    def _acquire_macos(cls, lock_path: Path) -> "SingleInstance":
        try:
            import fcntl
        except ImportError as exc:  # pragma: no cover - always present on macOS
            raise SingleInstanceError("macOS flock 不可用") from exc

        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise SingleInstanceError("macOS 安全文件打开标志不可用")
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | nofollow
        created = False
        try:
            try:
                handle = os.open(lock_path, flags | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
            except FileExistsError:
                handle = os.open(lock_path, flags)
        except OSError as exc:
            raise SingleInstanceError("无法安全打开 macOS 单实例锁") from exc

        try:
            if created:
                os.fchmod(handle, 0o600)
            value = os.fstat(handle)
            if not stat.S_ISREG(value.st_mode):
                raise SingleInstanceError("macOS 单实例锁不是普通文件")
            if value.st_uid != os.getuid():
                raise SingleInstanceError("macOS 单实例锁不属于当前用户")
            if stat.S_IMODE(value.st_mode) != 0o600:
                raise SingleInstanceError("macOS 单实例锁权限不安全")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    os.close(handle)
                    return cls(handle=None, already_running=True, kind="macos")
                raise SingleInstanceError("无法获取 macOS 单实例锁") from exc
            return cls(handle=handle, already_running=False, kind="macos")
        except BaseException:
            try:
                os.close(handle)
            except OSError:
                pass
            raise

    def close(self) -> None:
        if self.handle is None:
            self.handle = None
            return
        if self.kind == "windows":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (ctypes.c_void_p,)
            close_handle.restype = ctypes.c_bool
            close_handle(self.handle)
        elif self.kind == "macos":
            try:
                import fcntl

                fcntl.flock(self.handle, fcntl.LOCK_UN)
            finally:
                os.close(self.handle)
        self.handle = None

    def __enter__(self) -> "SingleInstance":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()


def existing_instance_is_alive(port: int, *, timeout: float = 1.5) -> bool:
    """Verify that the mutex owner is the expected local HTTP application."""

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/livez",
        headers={"User-Agent": "FastOrder-Local-Launcher/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        return False

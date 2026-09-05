from __future__ import annotations

import ctypes
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


MUTEX_NAME = r"Local\FastOrder.V1"
ERROR_ALREADY_EXISTS = 183


class SingleInstanceError(RuntimeError):
    """Raised when the Windows single-instance primitive cannot be created."""


@dataclass(slots=True)
class SingleInstance:
    """Own the product mutex for the lifetime of the process.

    Non-Windows development and tests use an inert instance. Production builds
    target Windows only and therefore always use the kernel mutex.
    """

    handle: int | None
    already_running: bool

    @classmethod
    def acquire(cls) -> "SingleInstance":
        if os.name != "nt":
            return cls(handle=None, already_running=False)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        create_mutex.restype = ctypes.c_void_p
        ctypes.set_last_error(0)
        handle = create_mutex(None, False, MUTEX_NAME)
        if not handle:
            raise SingleInstanceError(f"无法创建单实例锁（Windows 错误 {ctypes.get_last_error()}）")
        return cls(handle=int(handle), already_running=ctypes.get_last_error() == ERROR_ALREADY_EXISTS)

    def close(self) -> None:
        if self.handle is None or os.name != "nt":
            self.handle = None
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_bool
        close_handle(self.handle)
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

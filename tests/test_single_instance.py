from __future__ import annotations

import errno
import os
import stat
import sys
from types import SimpleNamespace

import pytest

from fast_order import single_instance


def test_non_windows_single_instance_is_inert(monkeypatch):
    monkeypatch.setattr(single_instance.os, "name", "posix")
    instance = single_instance.SingleInstance.acquire()
    assert instance.already_running is False
    assert instance.handle is None
    instance.close()


def test_existing_instance_probe_only_accepts_http_200(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(single_instance.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    assert single_instance.existing_instance_is_alive(8765)


def test_macos_single_instance_uses_private_nonblocking_flock(monkeypatch, tmp_path):
    calls = []

    class FakeFcntl:
        LOCK_EX = 1
        LOCK_NB = 2
        LOCK_UN = 4

        @staticmethod
        def flock(handle, operation):
            calls.append((handle, operation))

    monkeypatch.setattr(single_instance.sys, "platform", "darwin")
    monkeypatch.setattr(single_instance.os, "O_NOFOLLOW", 0x100, raising=False)
    monkeypatch.setattr(single_instance.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(single_instance.os, "open", lambda *_args: 17)
    monkeypatch.setattr(single_instance.os, "fchmod", lambda *_args: None, raising=False)
    monkeypatch.setattr(
        single_instance.os,
        "fstat",
        lambda _handle: SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=501),
    )
    monkeypatch.setattr(single_instance.os, "close", lambda handle: calls.append((handle, "close")))
    monkeypatch.setitem(sys.modules, "fcntl", FakeFcntl)

    instance = single_instance.SingleInstance.acquire(tmp_path / ".fastorder.lock")
    assert instance.already_running is False
    instance.close()
    assert (17, FakeFcntl.LOCK_EX | FakeFcntl.LOCK_NB) in calls
    assert (17, FakeFcntl.LOCK_UN) in calls


def test_macos_single_instance_reports_busy_lock(monkeypatch, tmp_path):
    class BusyFcntl:
        LOCK_EX = 1
        LOCK_NB = 2
        LOCK_UN = 4

        @staticmethod
        def flock(_handle, _operation):
            raise OSError(errno.EAGAIN, "busy")

    monkeypatch.setattr(single_instance.sys, "platform", "darwin")
    monkeypatch.setattr(single_instance.os, "O_NOFOLLOW", 0x100, raising=False)
    monkeypatch.setattr(single_instance.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(single_instance.os, "open", lambda *_args: 18)
    monkeypatch.setattr(single_instance.os, "fchmod", lambda *_args: None, raising=False)
    monkeypatch.setattr(
        single_instance.os,
        "fstat",
        lambda _handle: SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=501),
    )
    monkeypatch.setattr(single_instance.os, "close", lambda _handle: None)
    monkeypatch.setitem(sys.modules, "fcntl", BusyFcntl)

    instance = single_instance.SingleInstance.acquire(tmp_path / ".fastorder.lock")
    assert instance.already_running is True
    assert instance.handle is None


def test_macos_single_instance_rejects_unsafe_existing_lock(monkeypatch, tmp_path):
    class FakeFcntl:
        LOCK_EX = 1
        LOCK_NB = 2
        LOCK_UN = 4

    closed = []
    monkeypatch.setattr(single_instance.sys, "platform", "darwin")
    monkeypatch.setattr(single_instance.os, "O_NOFOLLOW", 0x100, raising=False)
    monkeypatch.setattr(single_instance.os, "getuid", lambda: 501, raising=False)

    def existing_open(_path, flags, *_args):
        if flags & os.O_EXCL:
            raise FileExistsError()
        return 19

    monkeypatch.setattr(single_instance.os, "open", existing_open)
    monkeypatch.setattr(
        single_instance.os,
        "fstat",
        lambda _handle: SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=501),
    )
    monkeypatch.setattr(single_instance.os, "close", lambda handle: closed.append(handle))
    monkeypatch.setitem(sys.modules, "fcntl", FakeFcntl)

    with pytest.raises(single_instance.SingleInstanceError, match="权限不安全"):
        single_instance.SingleInstance.acquire(tmp_path / ".fastorder.lock")
    assert closed == [19]

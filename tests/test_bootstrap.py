from __future__ import annotations

import logging
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fast_order import bootstrap


def test_resource_root_uses_executable_directory_in_nuitka_standalone(monkeypatch, tmp_path: Path):
    executable = tmp_path / "FastOrder.dist" / "FastOrder.exe"
    monkeypatch.setattr(bootstrap, "__compiled__", SimpleNamespace(standalone=True), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    assert bootstrap.resource_root() == executable.parent.resolve()


def test_prepare_paths_rejects_acl_failure(monkeypatch, tmp_path: Path):
    paths = SimpleNamespace(
        data_root=tmp_path / "data",
        runtime_root=tmp_path / "data" / "runtime",
        broker_tokens=tmp_path / "data" / "runtime" / "broker_tokens",
        logs=tmp_path / "data" / "logs",
        ensure_directories=lambda: None,
        migrate_legacy=lambda _root: None,
    )
    paths.data_root.mkdir(parents=True)
    monkeypatch.setattr(bootstrap, "harden_windows_acl", lambda _path: False)
    with pytest.raises(RuntimeError, match="无法保护"):
        bootstrap.prepare_writable_paths(paths, legacy_root=tmp_path)


def test_production_logging_writes_only_to_injected_directory(tmp_path: Path):
    logs = tmp_path / "user-data" / "logs"
    bootstrap.configure_production_logging(logs)
    logging.getLogger("fast_order.test").warning("safe test record")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "safe test record" in (logs / "fast_order.log").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_occupied_port_fails_before_application_initialization(monkeypatch, tmp_path: Path):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    import app

    monkeypatch.setattr(app, "FastOrderApplication", lambda *_args, **_kwargs: pytest.fail("initialized"))
    settings = SimpleNamespace(port=port)
    try:
        with pytest.raises(RuntimeError, match="已被其他程序占用"):
            await bootstrap.serve(settings, no_browser=True)
    finally:
        listener.close()

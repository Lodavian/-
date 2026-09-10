from __future__ import annotations

from pathlib import Path

from fast_order import bootstrap


def test_resource_root_detects_macos_app_resources(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "FastOrder.app" / "Contents" / "MacOS" / "FastOrder"
    resources = executable.parent.parent / "Resources"
    resources.mkdir(parents=True)
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(bootstrap.sys, "platform", "darwin")
    monkeypatch.setattr(bootstrap.sys, "executable", str(executable))

    assert bootstrap.resource_root() == resources.resolve()

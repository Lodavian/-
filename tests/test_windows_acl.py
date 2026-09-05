from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import fast_order.credentials as credentials


def test_harden_windows_acl_uses_sid_and_binary_subprocess_output(
    monkeypatch, tmp_path: Path
) -> None:
    target = tmp_path / "runtime"
    target.mkdir()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((command, kwargs))
        if command[0] == "whoami":
            return SimpleNamespace(
                returncode=0,
                stdout=b'"DESKTOP\\\xd3\xc3\xbb\xa7","S-1-5-21-100-200-300-1001"\r\n',
            )
        return SimpleNamespace(returncode=0, stdout=b"processed successfully\r\n")

    monkeypatch.setattr(credentials.os, "name", "nt")
    monkeypatch.setattr(credentials.subprocess, "run", fake_run)

    assert credentials.harden_windows_acl(target) is True
    assert calls[0][0] == ["whoami", "/user", "/fo", "csv", "/nh"]
    assert calls[0][1]["text"] is False
    assert calls[1][1]["text"] is False
    assert "*S-1-5-21-100-200-300-1001:(OI)(CI)F" in calls[1][0]


def test_harden_windows_acl_rejects_missing_sid(
    monkeypatch, tmp_path: Path
) -> None:
    target = tmp_path / "runtime"
    target.mkdir()
    monkeypatch.setattr(credentials.os, "name", "nt")
    monkeypatch.setattr(
        credentials.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=b"garbled"),
    )

    assert credentials.harden_windows_acl(target) is False

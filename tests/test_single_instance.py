from __future__ import annotations

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

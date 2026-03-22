"""Tests for runserver argument handling in ``manage.main``."""

from __future__ import annotations

import pytest

import manage


@pytest.mark.pr(6201)
def test_main_preserves_django_runserver_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runserver without addrport should retain Django's implicit bind and port."""

    recorded: dict[str, object] = {}

    def fake_run_runserver(base_dir, argv, is_debug_session) -> None:
        recorded["base_dir"] = base_dir
        recorded["argv"] = list(argv)
        recorded["is_debug_session"] = is_debug_session

    monkeypatch.setattr(manage, "_run_runserver", fake_run_runserver)

    manage.main(["runserver"])

    assert recorded["argv"] == ["runserver", "--noreload"]
    assert recorded["is_debug_session"] is False


@pytest.mark.pr(6201)
def test_main_preserves_explicit_runserver_addrport(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runserver with an explicit addrport should keep the provided binding intact."""

    recorded: dict[str, object] = {}

    def fake_run_runserver(base_dir, argv, is_debug_session) -> None:
        recorded["base_dir"] = base_dir
        recorded["argv"] = list(argv)
        recorded["is_debug_session"] = is_debug_session

    monkeypatch.setattr(manage, "_run_runserver", fake_run_runserver)

    manage.main(["runserver", "127.0.0.1:9000"])

    assert recorded["argv"] == ["runserver", "--noreload", "127.0.0.1:9000"]
    assert recorded["is_debug_session"] is False

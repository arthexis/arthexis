"""Tests for Celery process startup behavior in ``manage.main``."""

from __future__ import annotations

from pathlib import Path

import pytest

import manage


pytestmark = pytest.mark.pr_origin(6301)


def test_main_does_not_spawn_celery_for_non_runserver_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Celery worker/beat should not be spawned for non-runserver commands."""

    popen_calls: list[list[str]] = []

    class _Proc:
        def terminate(self) -> None:
            return None

    def _fake_popen(cmd: list[str]) -> _Proc:
        popen_calls.append(cmd)
        return _Proc()

    monkeypatch.setattr(manage, "loadenv", lambda: None)
    monkeypatch.setattr(manage, "bootstrap_sqlite_driver", lambda: None)
    monkeypatch.setattr(manage, "_execute_django", lambda _argv, _base_dir: None)
    monkeypatch.setattr(manage, "_run_runserver", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manage.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(Path, "exists", lambda self: self.name == "celery.lck")

    manage.main(["check"])

    assert popen_calls == []


def test_main_spawns_celery_for_runserver_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runserver should still launch worker and beat when Celery is enabled."""

    popen_calls: list[list[str]] = []

    class _Proc:
        def terminate(self) -> None:
            return None

    def _fake_popen(cmd: list[str]) -> _Proc:
        popen_calls.append(cmd)
        return _Proc()

    monkeypatch.setattr(manage, "loadenv", lambda: None)
    monkeypatch.setattr(manage, "bootstrap_sqlite_driver", lambda: None)
    monkeypatch.setattr(manage, "_execute_django", lambda _argv, _base_dir: None)
    monkeypatch.setattr(manage, "_run_runserver", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(manage.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(Path, "exists", lambda self: self.name == "celery.lck")

    manage.main(["runserver", "--noreload"])

    assert len(popen_calls) == 2
    assert any("worker" in command for command in popen_calls)
    assert any("beat" in command for command in popen_calls)

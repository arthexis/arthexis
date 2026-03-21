"""Tests for Celery process startup behavior in ``manage.main``."""

from __future__ import annotations

from pathlib import Path

import pytest

import manage


@pytest.fixture
def mock_manage_main(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Mock ``manage.main`` dependencies and capture spawned process commands."""
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

    return popen_calls


def test_main_does_not_spawn_celery_for_non_runserver_commands(
    mock_manage_main: list[list[str]],
) -> None:
    """Celery worker/beat should not be spawned for non-runserver commands."""

    manage.main(["check"])

    assert mock_manage_main == []


def test_main_spawns_celery_for_runserver_when_enabled(
    mock_manage_main: list[list[str]],
) -> None:
    """Runserver should still launch worker and beat when Celery is enabled."""

    manage.main(["runserver", "--noreload"])

    assert len(mock_manage_main) == 2
    assert any("worker" in command for command in mock_manage_main)
    assert any("beat" in command for command in mock_manage_main)

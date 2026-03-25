"""Regression tests for Celery startup behavior in manage.main."""

from __future__ import annotations

from pathlib import Path

import manage


def test_main_starts_worker_and_beat_only_for_runserver(monkeypatch) -> None:
    """Celery worker/beat should spawn for runserver when celery lock is present."""

    monkeypatch.setattr(manage, "loadenv", lambda: None)
    monkeypatch.setattr(manage, "bootstrap_sqlite_driver", lambda: None)
    monkeypatch.setattr(Path, "exists", lambda self: str(self).endswith(".locks/celery.lck"))

    popen_commands: list[list[str]] = []

    class DummyProcess:
        def terminate(self) -> None:
            return None

    monkeypatch.setattr(
        manage.subprocess,
        "Popen",
        lambda command: popen_commands.append(command) or DummyProcess(),
    )
    monkeypatch.setattr(manage, "_run_runserver", lambda base_dir, args, is_debug: None)
    monkeypatch.setattr(manage, "_execute_django", lambda argv, base_dir: None)

    manage.main(["runserver", "--noreload"])

    assert len(popen_commands) == 2
    assert popen_commands[0][2:4] == ["celery", "-A"]
    assert popen_commands[0][4:6] == ["config", "worker"]
    assert popen_commands[1][4:6] == ["config", "beat"]


def test_main_skips_celery_for_non_runserver_commands(monkeypatch) -> None:
    """Non-runserver commands should not spawn celery background processes."""

    monkeypatch.setattr(manage, "loadenv", lambda: None)
    monkeypatch.setattr(manage, "bootstrap_sqlite_driver", lambda: None)
    monkeypatch.setattr(Path, "exists", lambda self: str(self).endswith(".locks/celery.lck"))

    popen_calls: list[list[str]] = []
    monkeypatch.setattr(manage.subprocess, "Popen", lambda command: popen_calls.append(command))
    monkeypatch.setattr(manage, "_run_runserver", lambda base_dir, args, is_debug: None)
    monkeypatch.setattr(manage, "_execute_django", lambda argv, base_dir: None)

    manage.main(["check"])

    assert popen_calls == []

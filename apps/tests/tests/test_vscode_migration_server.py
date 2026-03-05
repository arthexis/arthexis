"""Regression tests for VS Code migration launcher interrupt handling."""

from __future__ import annotations

import subprocess

import pytest

from apps.vscode import migration_server


class FakeProcess:
    """Simple process double for migration subprocess behavior."""

    def __init__(self, communicate_effect: object = ("", ""), return_code: int = 0) -> None:
        self.communicate_effect = communicate_effect
        self.return_code = return_code
        self.terminate_calls = 0
        self.kill_calls = 0

    def communicate(self) -> tuple[str, str]:
        """Return command output or raise the configured effect."""

        if isinstance(self.communicate_effect, BaseException):
            raise self.communicate_effect
        return self.communicate_effect

    def wait(self) -> int:
        """Return a configured process return code."""

        return self.return_code

    def terminate(self) -> None:
        """Record graceful termination requests."""

        self.terminate_calls += 1

    def kill(self) -> None:
        """Record forceful termination requests."""

        self.kill_calls += 1


def test_run_migrations_returns_130_on_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: migration launcher should handle Ctrl+C without traceback."""

    process = FakeProcess(communicate_effect=KeyboardInterrupt())
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)

    code = migration_server.run_migrations()

    assert code == 130
    assert process.terminate_calls == 1
    assert process.kill_calls == 0


def test_run_migrations_kills_process_if_second_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: repeated Ctrl+C should force kill migration subprocess."""

    class DoubleInterruptProcess(FakeProcess):
        def __init__(self) -> None:
            super().__init__(communicate_effect=KeyboardInterrupt(), return_code=0)
            self.wait_calls = 0

        def wait(self) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise KeyboardInterrupt()
            return 0

    process = DoubleInterruptProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)

    code = migration_server.run_migrations()

    assert code == 130
    assert process.terminate_calls == 1
    assert process.kill_calls == 1

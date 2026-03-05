from __future__ import annotations

import subprocess

import pytest

from apps.vscode import test_server


class FakeProcess:
    """Simple process double for launcher subprocess behavior."""

    def __init__(self, wait_effect: object = 0) -> None:
        self.wait_effect = wait_effect
        self.terminate_calls = 0
        self.kill_calls = 0

    def wait(self) -> int:
        """Return a code or raise the configured effect."""

        if isinstance(self.wait_effect, BaseException):
            raise self.wait_effect
        return int(self.wait_effect)

    def terminate(self) -> None:
        """Record graceful termination requests."""

        self.terminate_calls += 1

    def kill(self) -> None:
        """Record forceful termination requests."""

        self.kill_calls += 1


def test_run_tests_returns_130_on_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: launcher should handle Ctrl+C without a traceback."""

    process = FakeProcess(wait_effect=KeyboardInterrupt())

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)

    code = test_server.run_tests()

    assert code == 130
    assert process.terminate_calls == 1
    assert process.kill_calls == 0


def test_run_tests_kills_process_if_second_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: repeated Ctrl+C should force kill the pytest subprocess."""

    class DoubleInterruptProcess(FakeProcess):
        def __init__(self) -> None:
            super().__init__(wait_effect=0)
            self.wait_calls = 0

        def wait(self) -> int:
            self.wait_calls += 1
            if self.wait_calls <= 2:
                raise KeyboardInterrupt()
            return 1

    process = DoubleInterruptProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)

    code = test_server.run_tests()

    assert code == 130
    assert process.terminate_calls == 1
    assert process.kill_calls == 1

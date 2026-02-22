"""Tests for the consolidated ``test`` management command."""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command


pytestmark = pytest.mark.regression


def test_test_run_delegates_to_pytest(monkeypatch) -> None:
    """Regression: ``test run`` should forward arguments to pytest execution."""

    captured: dict[str, list[str]] = {}

    def fake_run_pytest(self, pytest_args):
        captured["args"] = list(pytest_args)

    monkeypatch.setattr("apps.tests.management.commands.test.Command._run_pytest", fake_run_pytest)

    call_command("test", "run", "--", "-k", "smoke")

    assert captured["args"] == ["--", "-k", "smoke"]


def test_test_server_invokes_vscode_test_server(monkeypatch) -> None:
    """Regression: ``test server`` should call the test server helper."""

    called: dict[str, object] = {}

    def fake_server(self, *, interval: float, debounce: float, latest: bool) -> None:
        called.update(interval=interval, debounce=debounce, latest=latest)

    monkeypatch.setattr(
        "apps.tests.management.commands.test.Command._run_test_server",
        fake_server,
    )

    call_command("test", "server", "--interval", "2", "--debounce", "3", "--no-latest")

    assert called == {"interval": 2.0, "debounce": 3.0, "latest": False}


def test_test_command_rejects_unknown_action() -> None:
    """Regression: unsupported actions should raise a command error."""

    with pytest.raises(CommandError, match="Unsupported action"):
        command = __import__("apps.tests.management.commands.test", fromlist=["Command"]).Command()
        command.handle(action="invalid", pytest_args=[])

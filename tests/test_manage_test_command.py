from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.core.management.base import CommandError

from apps.tests.management.commands.test import Command


def test_run_pytest_requires_pytest_module(monkeypatch: pytest.MonkeyPatch) -> None:
    command = Command()

    monkeypatch.setattr(
        "apps.tests.management.commands.test.importlib.util.find_spec",
        lambda name: None if name == "pytest" else object(),
    )

    with pytest.raises(CommandError, match="pytest is not installed"):
        command._run_pytest([])


def test_run_pytest_forwards_remainder_args(monkeypatch: pytest.MonkeyPatch) -> None:
    command = Command()

    monkeypatch.setattr(
        "apps.tests.management.commands.test.importlib.util.find_spec",
        lambda _name: object(),
    )
    monkeypatch.setattr(
        "apps.tests.management.commands.test.resolve_project_python",
        lambda _base_dir: ".venv/bin/python",
    )

    captured: dict[str, object] = {}

    def fake_run(command_args, *, cwd, env):
        captured["command_args"] = command_args
        captured["cwd"] = cwd
        captured["env"] = env
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("apps.tests.management.commands.test.subprocess.run", fake_run)

    command._run_pytest(["--", "-k", "netmesh"])

    command_args = captured["command_args"]
    assert command_args[-2:] == ["-k", "netmesh"]

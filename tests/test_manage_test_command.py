from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.core.management.base import CommandError

from apps.tests.management.commands.test import Command


def test_run_pytest_requires_pytest_module(monkeypatch: pytest.MonkeyPatch) -> None:
    command = Command()
    monkeypatch.setattr(command, "_base_dir", lambda: "/tmp/repo")
    monkeypatch.setattr(
        "apps.tests.management.commands.test.resolve_project_python",
        lambda _base_dir: ".venv/bin/python",
    )

    def fake_run(command_args, *, cwd, env):
        assert command_args[0] == ".venv/bin/python"
        assert command_args[1:3] == [
            "-c",
            "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('pytest') else 1)",
        ]
        assert cwd == "/tmp/repo"
        assert env
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("apps.tests.management.commands.test.subprocess.run", fake_run)

    with pytest.raises(CommandError, match="pytest is not installed"):
        command._run_pytest([])



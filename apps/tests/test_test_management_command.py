"""Tests for the consolidated ``test`` management command."""

from __future__ import annotations

import pytest


@pytest.mark.django_db
def test_run_subcommand_uses_validated_project_python(monkeypatch) -> None:
    """Regression: ``test run`` should prefer the repository virtualenv interpreter."""

    from apps.tests.management.commands.test import Command

    captured: dict[str, object] = {}

    def fake_run(command, cwd=None, env=None):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["env"] = env

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(
        "apps.tests.management.commands.test.resolve_project_python",
        lambda base_dir: "/tmp/project/.venv/bin/python",
    )
    monkeypatch.setattr("subprocess.run", fake_run)

    Command()._run_pytest(["--", "-k", "smoke"])

    assert captured["command"] == [
        "/tmp/project/.venv/bin/python",
        "-m",
        "pytest",
        "-k",
        "smoke",
    ]

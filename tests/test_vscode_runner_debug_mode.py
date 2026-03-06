"""Regression tests for VS Code migration/test runners forcing non-debug subprocesses."""

from __future__ import annotations

import os

from apps.vscode import migration_server, test_server


def test_migration_runner_subprocess_env_disables_debug(monkeypatch) -> None:
    """Migration runner should force debug mode off in subprocess environment."""

    monkeypatch.setenv("DEBUG", "1")
    monkeypatch.setenv("DJANGO_DEBUG", "1")

    env = migration_server._build_subprocess_env()

    assert env["DEBUG"] == "0"
    assert env["DJANGO_DEBUG"] == "0"
    assert env["PATH"] == os.environ["PATH"]


def test_test_runner_subprocess_env_disables_debug(monkeypatch) -> None:
    """Test runner should force debug mode off in subprocess environment."""

    monkeypatch.setenv("DEBUG", "1")
    monkeypatch.setenv("DJANGO_DEBUG", "1")

    env = test_server._build_subprocess_env()

    assert env["DEBUG"] == "0"
    assert env["DJANGO_DEBUG"] == "0"
    assert env["PATH"] == os.environ["PATH"]

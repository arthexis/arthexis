"""Tests for command execution behavior in the canonical command API."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest import MonkeyPatch

from utils import command_api


class _FakeResult(SimpleNamespace):
    """Simple subprocess result replacement used by tests."""



@pytest.mark.parametrize(
    ("celery_enabled", "command", "args", "expected_flag"),
    [
        (True, "feature", ["--kind", "suite"], "--celery"),
        (False, "chargers", [], "--no-celery"),
    ],
)
def test_run_command_forwards_celery_flag(
    monkeypatch: MonkeyPatch,
    celery_enabled: bool,
    command: str,
    args: list[str],
    expected_flag: str,
) -> None:
    """`run_command` should forward celery selector flags to manage.py invocations."""

    captured: dict[str, object] = {}

    def _fake_run(cmd: list[str], cwd: Path, check: bool) -> _FakeResult:
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["check"] = check
        return _FakeResult(returncode=0)

    monkeypatch.setattr(command_api.subprocess, "run", _fake_run)

    result = command_api.run_command(
        base_dir=Path("/tmp/project"),
        raw_command=command,
        command_args=args,
        options=command_api.CommandOptions(celery=celery_enabled, deprecated=False),
    )

    assert result == 0
    assert captured["cmd"] == [
        command_api.sys.executable,
        "manage.py",
        expected_flag,
        command,
        *args,
    ]
    assert captured["check"] is False
    assert captured["cwd"] == Path("/tmp/project")

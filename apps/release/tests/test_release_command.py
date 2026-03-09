"""Regression tests for consolidated release management commands."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

def test_release_clean_logs_without_targets_raises_command_error() -> None:
    """Regression: ``release clean-logs`` should preserve command error semantics."""

    with pytest.raises(CommandError, match="Specify --all"):
        call_command("release", "clean-logs")

def test_release_run_data_transforms_invokes_all_registered(monkeypatch) -> None:
    """Regression: ``release run-data-transforms`` should run all discovered transforms."""

    monkeypatch.setattr(
        "apps.release.management.commands.release.list_transform_names",
        lambda: ["first", "second"],
    )

    captured: list[tuple[str, int]] = []

    def fake_runner(self, name: str, *, max_batches: int) -> None:
        captured.append((name, max_batches))

    monkeypatch.setattr(
        "apps.release.management.commands.release.Command._run_transform_batches",
        fake_runner,
    )

    call_command("release", "run-data-transforms", "--max-batches", "2")

    assert captured == [("first", 2), ("second", 2)]


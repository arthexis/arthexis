"""Regression tests for deferred release data transforms command."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


pytestmark = pytest.mark.regression


def test_run_release_data_transforms_runs_until_complete(monkeypatch) -> None:
    """Regression: command should stop early when a transform reports completion."""

    calls: list[str] = []

    class Result:
        def __init__(self, complete: bool) -> None:
            self.complete = complete
            self.processed = 1
            self.updated = 1

    outcomes = [Result(False), Result(True), Result(True)]

    def fake_run_transform(name: str):
        calls.append(name)
        return outcomes[len(calls) - 1]

    monkeypatch.setattr(
        "apps.release.management.commands.run_release_data_transforms.run_transform",
        fake_run_transform,
    )

    call_command("run_release_data_transforms", "release.normalize_package_release_versions", "--max-batches", "3")

    assert calls == ["release.normalize_package_release_versions", "release.normalize_package_release_versions"]


def test_run_release_data_transforms_rejects_invalid_batches() -> None:
    """Regression: command should reject invalid --max-batches values."""

    with pytest.raises(CommandError):
        call_command("run_release_data_transforms", "--max-batches", "0")

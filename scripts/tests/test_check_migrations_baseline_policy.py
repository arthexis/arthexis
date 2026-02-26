"""Regression tests for migration depth policy in check_migrations."""

from types import SimpleNamespace

import pytest

from scripts import check_migrations

pytestmark = pytest.mark.regression


def test_check_baseline_depths_fails_without_recent_squash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apps above threshold without recent squash should fail the baseline check."""

    status = SimpleNamespace(
        active_chain_depth=30,
        latest_number=30,
        latest_squash_number=20,
        exceeds_threshold=lambda threshold: True,
        has_recent_squash=lambda recent_window: False,
    )
    monkeypatch.setattr(check_migrations, "evaluate_app_baseline", lambda app, repo_root: status)

    assert check_migrations._check_baseline_depths(["nodes"], threshold=10, recent_window=3) == 1


def test_check_baseline_depths_passes_with_recent_squash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apps above threshold with recent squash should pass the baseline check."""

    status = SimpleNamespace(
        active_chain_depth=30,
        latest_number=30,
        latest_squash_number=29,
        exceeds_threshold=lambda threshold: True,
        has_recent_squash=lambda recent_window: True,
    )
    monkeypatch.setattr(check_migrations, "evaluate_app_baseline", lambda app, repo_root: status)

    assert check_migrations._check_baseline_depths(["nodes"], threshold=10, recent_window=3) == 0

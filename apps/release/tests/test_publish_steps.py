"""Regression checks for shared release publish step orchestration."""

from __future__ import annotations

from apps.core.views.reports.release_publish import pipeline as UI_PIPELINE
from apps.core.views.reports.release_publish.views import (
    PUBLISH_STEPS as UI_PUBLISH_STEPS,
)
from apps.release.domain import PUBLISH_STEPS as DOMAIN_PUBLISH_STEPS
from apps.release.publishing import pipeline as RELEASE_PIPELINE
from apps.release.release_workflow import _build_release_workflow

EXPECTED_STEP_ORDER = [
    "Check version number availability",
    "Freeze, squash and approve migrations",
    "Execute pre-release actions",
    "Build release artifacts",
    "Complete test suite with --all flag",
    "Prune worst 1% of tests by PR",
    "Confirm PyPI Trusted Publisher settings",
    "Verify release environment",
    "Export artifacts and push release tag",
    "Wait for GitHub Actions publish",
    "Record publish URLs & update fixtures",
    "Capture PyPI publish logs",
]


def test_release_publish_steps_share_canonical_order() -> None:
    assert [name for name, _handler in DOMAIN_PUBLISH_STEPS] == EXPECTED_STEP_ORDER
    assert [name for name, _handler in UI_PUBLISH_STEPS] == EXPECTED_STEP_ORDER


def test_core_release_publish_pipeline_path_is_adapter() -> None:
    assert UI_PIPELINE is RELEASE_PIPELINE
    assert UI_PUBLISH_STEPS is RELEASE_PIPELINE.PUBLISH_STEPS


def test_headless_release_workflow_uses_canonical_order() -> None:
    workflow = _build_release_workflow()

    assert [step.name for step in workflow.steps] == EXPECTED_STEP_ORDER


def test_test_pruning_step_order_is_intentional() -> None:
    step_names = [name for name, _handler in DOMAIN_PUBLISH_STEPS]

    assert step_names.index("Complete test suite with --all flag") < step_names.index(
        "Prune worst 1% of tests by PR"
    )
    assert step_names.index("Prune worst 1% of tests by PR") < step_names.index(
        "Confirm PyPI Trusted Publisher settings"
    )

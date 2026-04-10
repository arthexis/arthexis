"""Regression checks for shared release publish step orchestration."""

from __future__ import annotations

from apps.core.views.reports.release_publish.views import (
    PUBLISH_STEPS as UI_PUBLISH_STEPS,
)
from apps.release.domain import PUBLISH_STEPS as DOMAIN_PUBLISH_STEPS
from apps.release.release_workflow import _build_release_workflow

EXPECTED_STEP_ORDER = [
    "Check version number availability",
    "Freeze, squash and approve migrations",
    "Execute pre-release actions",
    "Build release artifacts",
    "Complete test suite with --all flag",
    "Confirm PyPI Trusted Publisher settings",
    "Verify release environment",
    "Export artifacts and push release tag",
    "Wait for GitHub Actions publish",
    "Record publish URLs & update fixtures",
    "Capture PyPI publish logs",
]

def test_release_publish_step_order_matches_expected_order() -> None:
    assert [name for name, _handler_name in DOMAIN_PUBLISH_STEPS] == EXPECTED_STEP_ORDER


def test_release_publish_step_order_matches_expected_ui_order() -> None:
    assert [name for name, _func in UI_PUBLISH_STEPS] == EXPECTED_STEP_ORDER


def test_run_headless_publish_uses_expected_release_step_order() -> None:
    workflow = _build_release_workflow()

    assert [step.name for step in workflow.steps] == EXPECTED_STEP_ORDER


def test_release_publish_handler_names_match_ui_implementations() -> None:
    assert [handler_name for _name, handler_name in DOMAIN_PUBLISH_STEPS] == [
        func.__name__ for _name, func in UI_PUBLISH_STEPS
    ]

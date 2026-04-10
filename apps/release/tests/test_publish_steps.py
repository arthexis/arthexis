"""Regression checks for shared release publish step orchestration."""

from __future__ import annotations

from apps.core.views.reports.release_publish.views import PUBLISH_STEPS as UI_PUBLISH_STEPS
from apps.release.domain import PUBLISH_STEPS as DOMAIN_PUBLISH_STEPS
from apps.release.release_workflow import _build_release_workflow


def test_release_publish_step_order_matches_domain_and_ui() -> None:
    assert [name for name, _func in DOMAIN_PUBLISH_STEPS] == [
        name for name, _func in UI_PUBLISH_STEPS
    ]


def test_run_headless_publish_uses_canonical_release_step_order() -> None:
    workflow = _build_release_workflow()

    assert [step.name for step in workflow.steps] == [
        name for name, _func in DOMAIN_PUBLISH_STEPS
    ]

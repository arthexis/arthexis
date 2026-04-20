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


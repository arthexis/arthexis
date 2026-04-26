"""Canonical release publish step definitions.

The release domain owns authoritative publish step ordering. Adapters in the
admin report pipeline and the headless scheduler resolve step handlers to their
runtime implementations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

BUILD_RELEASE_ARTIFACTS_STEP_NAME = "Build release artifacts"
FIXTURE_REVIEW_STEP_NAME = "Freeze, squash and approve migrations"
TEST_PRUNING_STEP_NAME = "Prune worst 1% of tests by PR"


class ReleaseStep(Protocol):
    def __call__(
        self,
        release: Any,
        ctx: dict,
        log_path: Path,
        *,
        user: Any = None,
    ) -> None: ...


PUBLISH_STEPS: list[tuple[str, str]] = [
    ("Check version number availability", "_step_check_version"),
    (FIXTURE_REVIEW_STEP_NAME, "_step_handle_migrations"),
    ("Execute pre-release actions", "_step_pre_release_actions"),
    (BUILD_RELEASE_ARTIFACTS_STEP_NAME, "_step_promote_build"),
    ("Complete test suite with --all flag", "_step_run_tests"),
    # Pruning evidence is reviewed after the full suite proves releasability and
    # before any external publish prerequisite can advance.
    (TEST_PRUNING_STEP_NAME, "_step_prune_low_value_tests"),
    (
        "Confirm PyPI Trusted Publisher settings",
        "_step_confirm_pypi_trusted_publisher_settings",
    ),
    ("Verify release environment", "_step_verify_release_environment"),
    (
        "Export artifacts and push release tag",
        "_step_export_and_dispatch",
    ),
    ("Wait for GitHub Actions publish", "_step_wait_for_github_actions_publish"),
    ("Record publish URLs & update fixtures", "_step_record_publish_metadata"),
    ("Capture PyPI publish logs", "_step_capture_publish_logs"),
]


__all__ = [
    "BUILD_RELEASE_ARTIFACTS_STEP_NAME",
    "FIXTURE_REVIEW_STEP_NAME",
    "PUBLISH_STEPS",
    "TEST_PRUNING_STEP_NAME",
]

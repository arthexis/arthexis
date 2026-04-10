"""Canonical release publish step definitions.

Release publish orchestration is shared between the admin report pipeline and
headless scheduler execution. Keep the ordered step list here so both adapters
consume the same release-domain contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

BUILD_RELEASE_ARTIFACTS_STEP_NAME = "Build release artifacts"
FIXTURE_REVIEW_STEP_NAME = "Freeze, squash and approve migrations"

ReleaseStep = Callable[[object, dict, Path], None]


def _run_pipeline_step(step_name: str, release, ctx: dict, log_path: Path, *, user=None):
    """Dispatch a canonical step to the pipeline adapter implementation."""

    from apps.core.views.reports.release_publish import pipeline

    step = getattr(pipeline, step_name)
    return step(release, ctx, log_path, user=user)


def _step_check_version(release, ctx: dict, log_path: Path, *, user=None) -> None:
    _run_pipeline_step("_step_check_version", release, ctx, log_path, user=user)


def _step_handle_migrations(release, ctx: dict, log_path: Path, *, user=None) -> None:
    _run_pipeline_step("_step_handle_migrations", release, ctx, log_path, user=user)


def _step_pre_release_actions(release, ctx: dict, log_path: Path, *, user=None) -> None:
    _run_pipeline_step("_step_pre_release_actions", release, ctx, log_path, user=user)


def _step_promote_build(release, ctx: dict, log_path: Path, *, user=None) -> None:
    _run_pipeline_step("_step_promote_build", release, ctx, log_path, user=user)


def _step_run_tests(release, ctx: dict, log_path: Path, *, user=None) -> None:
    _run_pipeline_step("_step_run_tests", release, ctx, log_path, user=user)


def _step_confirm_pypi_trusted_publisher_settings(
    release,
    ctx: dict,
    log_path: Path,
    *,
    user=None,
) -> None:
    _run_pipeline_step(
        "_step_confirm_pypi_trusted_publisher_settings",
        release,
        ctx,
        log_path,
        user=user,
    )


def _step_verify_release_environment(release, ctx: dict, log_path: Path, *, user=None) -> None:
    _run_pipeline_step("_step_verify_release_environment", release, ctx, log_path, user=user)


def _step_export_and_dispatch(release, ctx: dict, log_path: Path, *, user=None) -> None:
    _run_pipeline_step("_step_export_and_dispatch", release, ctx, log_path, user=user)


def _step_wait_for_github_actions_publish(
    release,
    ctx: dict,
    log_path: Path,
    *,
    user=None,
) -> None:
    _run_pipeline_step(
        "_step_wait_for_github_actions_publish",
        release,
        ctx,
        log_path,
        user=user,
    )


def _step_record_publish_metadata(release, ctx: dict, log_path: Path, *, user=None) -> None:
    _run_pipeline_step("_step_record_publish_metadata", release, ctx, log_path, user=user)


def _step_capture_publish_logs(release, ctx: dict, log_path: Path, *, user=None) -> None:
    _run_pipeline_step("_step_capture_publish_logs", release, ctx, log_path, user=user)


PUBLISH_STEPS: list[tuple[str, ReleaseStep]] = [
    ("Check version number availability", _step_check_version),
    (FIXTURE_REVIEW_STEP_NAME, _step_handle_migrations),
    ("Execute pre-release actions", _step_pre_release_actions),
    (BUILD_RELEASE_ARTIFACTS_STEP_NAME, _step_promote_build),
    ("Complete test suite with --all flag", _step_run_tests),
    (
        "Confirm PyPI Trusted Publisher settings",
        _step_confirm_pypi_trusted_publisher_settings,
    ),
    ("Verify release environment", _step_verify_release_environment),
    (
        "Export artifacts and push release tag",
        _step_export_and_dispatch,
    ),
    ("Wait for GitHub Actions publish", _step_wait_for_github_actions_publish),
    ("Record publish URLs & update fixtures", _step_record_publish_metadata),
    ("Capture PyPI publish logs", _step_capture_publish_logs),
]


__all__ = [
    "BUILD_RELEASE_ARTIFACTS_STEP_NAME",
    "FIXTURE_REVIEW_STEP_NAME",
    "PUBLISH_STEPS",
]

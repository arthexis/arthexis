"""Compatibility checks for refactored release publishing pipeline modules."""

from __future__ import annotations

from apps.release.publishing import pipeline
from apps.release.publishing.pipeline import (
    actions,
    git_ops,
    github_ops,
    logging_progress,
    progress,
    workflow,
)


def test_pipeline_package_keeps_existing_publish_steps_export() -> None:
    assert pipeline.PUBLISH_STEPS is actions.PUBLISH_STEPS


def test_pipeline_workflow_module_exports_workflow_types() -> None:
    assert workflow.ReleasePublishWorkflow is actions.ReleasePublishWorkflow
    assert workflow.ReleasePublishContext is actions.ReleasePublishContext


def test_pipeline_adapter_modules_reexport_expected_helpers() -> None:
    assert callable(git_ops.current_branch)
    assert callable(github_ops.parse_github_repository)
    assert callable(logging_progress._append_log)


def test_pipeline_progress_module_exports_expected_helpers() -> None:
    assert progress.build_release_guidance is actions.build_release_guidance
    assert progress._build_release_progress_context is actions._build_release_progress_context

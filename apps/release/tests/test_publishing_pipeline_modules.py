"""Compatibility checks for refactored release publishing pipeline modules."""

from __future__ import annotations

import os
import stat

from apps.release.publishing import pipeline
from apps.release.publishing.pipeline import (
    actions,
    git_ops,
    github_ops,
    logging_progress,
    progress,
    workflow,
)
from apps.release.services import uploader
from apps.release.services.uploader import _write_private_askpass


def test_private_askpass_is_created_with_owner_only_permissions(tmp_path) -> None:
    path = tmp_path / "askpass.sh"

    _write_private_askpass(path, "operator", "secret-value")

    assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert 'echo "secret-value"' in path.read_text(encoding="utf-8")


def test_private_askpass_restores_execute_permission_masked_by_umask(tmp_path) -> None:
    path = tmp_path / "askpass.sh"
    original_umask = os.umask(0o177)
    try:
        _write_private_askpass(path, "operator", "secret-value")
    finally:
        os.umask(original_umask)

    assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_private_askpass_falls_back_when_fchmod_is_unavailable(tmp_path, monkeypatch) -> None:
    path = tmp_path / "askpass.sh"
    monkeypatch.delattr(uploader.os, "fchmod", raising=False)

    _write_private_askpass(path, "operator", "secret-value")

    assert stat.S_IMODE(path.stat().st_mode) == 0o700


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

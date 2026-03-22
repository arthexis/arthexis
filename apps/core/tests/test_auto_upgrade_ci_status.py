"""Tests for auto-upgrade CI workflow status resolution."""

from pathlib import Path

from apps.core.tasks.auto_upgrade import tasks


def test_ci_status_for_revision_prefers_pollable_workflow_status(monkeypatch):
    """Use the workflow result when the pollable upgrade-gate workflow reports one."""

    monkeypatch.setattr(tasks, "_resolve_github_slug", lambda _base_dir: "acme/widget")
    monkeypatch.setattr(
        tasks,
        "_fetch_ci_workflow_status",
        lambda repo_slug, branch, workflow="ci.yml": "success",
    )
    monkeypatch.setattr(tasks, "_fetch_ci_status", lambda repo_slug, revision: "failure")

    assert tasks._ci_status_for_revision(Path("/tmp/repo"), "abc123", branch="main") == "success"


def test_ci_status_for_revision_falls_back_to_commit_status(monkeypatch):
    """Fall back to the combined commit status when no workflow result is available."""

    monkeypatch.setattr(tasks, "_resolve_github_slug", lambda _base_dir: "acme/widget")
    monkeypatch.setattr(
        tasks,
        "_fetch_ci_workflow_status",
        lambda repo_slug, branch, workflow="ci.yml": None,
    )
    monkeypatch.setattr(tasks, "_fetch_ci_status", lambda repo_slug, revision: "success")

    assert tasks._ci_status_for_revision(Path("/tmp/repo"), "abc123", branch="main") == "success"

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from django.template.loader import render_to_string

from apps.core.views.reports.release_publish import pipeline
from apps.core.views.reports.release_publish.exceptions import PublishPending


def test_manual_push_flow_registers_pending_on_auth_error(monkeypatch, tmp_path: Path):
    log_path = tmp_path / "publish.log"
    ctx: dict[str, object] = {}

    monkeypatch.setattr(pipeline, "_has_remote", lambda _remote: True)
    monkeypatch.setattr(pipeline, "_current_branch", lambda: "main")
    monkeypatch.setattr(pipeline, "_has_upstream", lambda _branch: True)
    monkeypatch.setattr(pipeline, "_current_git_revision", lambda: "abc123")

    def _run(*_args, **_kwargs):
        raise subprocess.CalledProcessError(
            returncode=128,
            cmd=["git", "push"],
            stderr="fatal: Authentication failed",
        )

    monkeypatch.setattr(pipeline.subprocess, "run", _run)

    with pytest.raises(PublishPending):
        pipeline._push_release_changes(
            log_path,
            ctx,
            step_name=pipeline.BUILD_RELEASE_ARTIFACTS_STEP_NAME,
        )

    pending = ctx.get("pending_git_push")
    assert isinstance(pending, dict)
    assert pending["branch"] == "main"
    assert ctx.get("paused") is True


def test_dirty_repo_gating_detects_stale_build(monkeypatch):
    ctx = {"build_revision": "old", "error": "boom"}
    steps = [(pipeline.BUILD_RELEASE_ARTIFACTS_STEP_NAME, object()), ("Later", object())]
    monkeypatch.setattr(pipeline, "_current_git_revision", lambda: "new")
    monkeypatch.setattr(pipeline, "_working_tree_dirty", lambda: False)

    assert pipeline._build_artifacts_stale(ctx, step_count=1, steps=steps) is True


def test_publish_workflow_polling_pauses_when_run_in_progress(monkeypatch, tmp_path: Path):
    class DummyRelease:
        version = "1.2.3"

    ctx: dict[str, object] = {}
    log_path = tmp_path / "publish.log"

    monkeypatch.setattr(pipeline, "_resolve_github_token", lambda *_args, **_kwargs: "token")
    monkeypatch.setattr(pipeline, "_resolve_github_repository", lambda _release: ("acme", "widget"))
    monkeypatch.setattr(
        pipeline,
        "_fetch_publish_workflow_run",
        lambda **_kwargs: {"id": 1, "status": "in_progress", "html_url": "https://example/run/1"},
    )

    with pytest.raises(PublishPending):
        pipeline._step_capture_publish_logs(DummyRelease(), ctx, log_path)

    assert ctx.get("publish_pending") is True
    assert ctx.get("publish_workflow_url") == "https://example/run/1"


def test_release_artifact_collection_finds_wheel_and_sdist(tmp_path: Path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "pkg-1.0.0-py3-none-any.whl"
    sdist = dist / "pkg-1.0.0.tar.gz"
    wheel.write_text("w")
    sdist.write_text("s")

    monkeypatch.chdir(tmp_path)
    artifacts = pipeline._collect_release_artifacts()

    assert {path.name for path in artifacts} == {wheel.name, sdist.name}
    assert len(artifacts) == 2


def test_release_progress_template_renders_github_token_prompt_conditionally():
    """Regression: release progress template must parse boolean token conditions without parentheses."""
    html = render_to_string(
        "core/release_progress.html",
        {
            "done": False,
            "github_credentials_missing": True,
            "github_token_using_stored": False,
            "github_token_required": True,
            "current_step": "publish",
            "step_states": [],
            "release": type("Release", (), {"pk": 1, "__str__": lambda self: "Release 1"})(),
            "action": "publish",
        },
    )

    assert "GitHub token required" in html

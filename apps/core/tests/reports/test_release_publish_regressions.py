from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from django.template.loader import render_to_string

from apps.core.views.reports.release_publish import pipeline
from apps.core.views.reports.release_publish.exceptions import PublishPending

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


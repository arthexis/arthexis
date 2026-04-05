from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from apps.core.views.reports.release_publish import pipeline
from apps.core.views.reports.release_publish.exceptions import PublishPending
from apps.core.views.reports.release_publish.workflow import ReleasePublishContext


def test_publish_workflow_polling_pauses_when_run_in_progress(monkeypatch, tmp_path: Path):
    class DummyRelease:
        pk = 1
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


def test_prepare_step_progress_invalid_restart_counter_defaults_to_zero(tmp_path: Path):
    restart_path = tmp_path / "release.restarts"
    restart_path.write_text("bad-counter", encoding="utf-8")

    restart_count, step_param = pipeline._prepare_step_progress(
        RequestFactory().get("/release/publish"),
        {"step": 4},
        restart_path,
        resume_requested=True,
    )

    assert restart_count == 0
    assert step_param == "4"


def test_current_git_revision_returns_empty_on_subprocess_failure(monkeypatch):
    def boom(_args):
        raise subprocess.CalledProcessError(returncode=2, cmd=["git", "rev-parse", "HEAD"])

    monkeypatch.setattr(pipeline, "_git_stdout", boom)

    assert pipeline._current_git_revision() == ""


def test_broadcast_release_message_logs_failures(monkeypatch, caplog):
    class DummyRelease:
        version = "1.2.3"

    monkeypatch.setattr(pipeline.Node, "get_local", lambda: None)
    monkeypatch.setattr(
        pipeline.NetMessage,
        "broadcast",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("broadcast offline")),
    )

    with caplog.at_level("ERROR"):
        pipeline._broadcast_release_message(DummyRelease())

    assert "Failed to broadcast release Net Message" in caplog.text


def test_release_progress_uses_mutated_context_for_advance(monkeypatch, tmp_path: Path):
    class DummyRelease:
        pk = 1
        version = "1.2.3"

        @staticmethod
        def to_credentials():
            return object()

        @staticmethod
        def uses_oidc_publishing():
            return True

    captured: dict[str, ReleasePublishContext] = {}

    class FakeWorkflow:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def load(_log_warning):
            return (
                ReleasePublishContext(step=0, started=True, paused=False, extras={}),
                None,
            )

        @staticmethod
        def template_state(ctx: ReleasePublishContext):
            return ctx.to_dict()

        @staticmethod
        def start(ctx: ReleasePublishContext, *, start_enabled: bool):
            assert start_enabled is False
            return ctx

        @staticmethod
        def resume(ctx: ReleasePublishContext):
            return ctx, False, None

        @staticmethod
        def step_progress(ctx: ReleasePublishContext, *, resume_requested: bool):
            assert resume_requested is False
            return 0, None

        @staticmethod
        def poll(ctx: ReleasePublishContext):
            return False, False

        @staticmethod
        def advance(*, ctx: ReleasePublishContext, **_kwargs):
            captured["ctx"] = ctx
            return ctx, ctx.step

        @staticmethod
        def persist_state(ctx: ReleasePublishContext, *, done: bool):
            assert done is False

    monkeypatch.setattr(pipeline, "ReleasePublishWorkflow", FakeWorkflow)
    monkeypatch.setattr(pipeline, "_get_release_or_response", lambda *_args: (DummyRelease(), None))
    monkeypatch.setattr(pipeline, "_resolve_release_log_dir", lambda _path: (tmp_path, None))
    monkeypatch.setattr(pipeline, "_handle_release_sync", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "_handle_release_restart", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        pipeline,
        "_prepare_logging",
        lambda ctx, *_args, **_kwargs: (ctx, tmp_path / "publish.log", ctx["step"]),
    )
    monkeypatch.setattr(pipeline, "_build_artifacts_stale", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        pipeline,
        "_handle_dirty_repository_action",
        lambda _request, ctx, _log_path: {
            **ctx,
            "paused": True,
            "pending_git_push": {"branch": "main"},
        },
    )
    monkeypatch.setattr(
        pipeline,
        "_handle_manual_git_push_action",
        lambda _request, ctx, _log_path: ctx,
    )
    monkeypatch.setattr(pipeline, "_resolve_release_log_display", lambda *_args, **_kwargs: (False, ""))
    monkeypatch.setattr(pipeline, "_resolve_next_step", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "_build_release_step_states", lambda **_kwargs: [])
    monkeypatch.setattr(pipeline, "_get_user_github_token", lambda _user: None)
    monkeypatch.setattr(pipeline, "_resolve_github_token", lambda *_args, **_kwargs: "token")
    monkeypatch.setattr(pipeline, "build_release_guidance", lambda **_kwargs: {})
    monkeypatch.setattr(pipeline, "_build_release_progress_context", lambda **_kwargs: {})
    monkeypatch.setattr(
        pipeline,
        "_finalize_release_progress_response",
        lambda **_kwargs: HttpResponse("ok"),
    )

    request = RequestFactory().get("/release/publish")
    request.user = type("User", (), {"is_authenticated": False})()
    response = pipeline.release_progress_impl(request, pk=1, action="publish")

    assert response.status_code == 200
    assert captured["ctx"].paused is True
    assert captured["ctx"].extras["pending_git_push"] == {"branch": "main"}


def test_resolve_safe_child_path_rejects_parent_traversal(tmp_path: Path):
    with pytest.raises(ValueError):
        pipeline._resolve_safe_child_path(tmp_path, "../escape.txt")


def test_release_progress_returns_400_for_invalid_state_path(monkeypatch):
    class DummyRelease:
        pk = 1

    def raise_unsafe_path(*_args, **_kwargs):
        raise ValueError("unsafe")

    monkeypatch.setattr(pipeline, "_get_release_or_response", lambda *_args: (DummyRelease(), None))
    monkeypatch.setattr(
        pipeline,
        "_resolve_safe_child_path",
        raise_unsafe_path,
    )
    monkeypatch.setattr(
        pipeline,
        "_render_release_progress_error",
        lambda *_args, **_kwargs: HttpResponse("bad path", status=400),
    )

    request = RequestFactory().get("/release/publish")
    request.user = type("User", (), {"is_authenticated": False})()

    response = pipeline.release_progress_impl(request, pk=1, action="publish")

    assert response.status_code == 400

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse

import apps.core.views.reports.release_publish.workflow as workflow_module
from apps.core.views.reports.release_publish import pipeline
from apps.core.views.reports.release_publish.exceptions import PublishPending
from apps.core.views.reports.release_publish.workflow import ReleasePublishContext
from apps.release import RepositoryTarget
from apps.release.models import Package, PackageRelease


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_release_repo(cwd: Path, version: str) -> None:
    _run_git(cwd, "init")
    _run_git(cwd, "config", "user.email", "release@example.test")
    _run_git(cwd, "config", "user.name", "Release Tester")
    (cwd / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    _run_git(cwd, "add", "VERSION")
    _run_git(cwd, "commit", "-m", f"version {version}")


def _publish_workflow_jobs() -> dict[str, object]:
    return _workflow_data("publish.yml")["jobs"]


def _workflow_data(filename: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[4]
    workflow_path = repo_root / ".github" / "workflows" / filename
    return pipeline.yaml.safe_load(workflow_path.read_text(encoding="utf-8")) or {}


def _workflow_on(workflow: dict[str, object]) -> object:
    return workflow.get("on", workflow.get(True, {}))


def _workflow_step(job: dict[str, object], name: str) -> dict[str, object]:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_publish_workflow_polling_pauses_when_run_in_progress(
    monkeypatch, tmp_path: Path
):
    class DummyRelease:
        pk = 1
        version = "1.2.3"

    ctx: dict[str, object] = {}
    log_path = tmp_path / "publish.log"

    monkeypatch.setattr(
        pipeline, "_resolve_github_token", lambda *_args, **_kwargs: "token"
    )
    monkeypatch.setattr(
        pipeline, "_resolve_github_repository", lambda _release: ("acme", "widget")
    )
    monkeypatch.setattr(
        pipeline,
        "_fetch_publish_workflow_run",
        lambda **_kwargs: {
            "id": 1,
            "status": "in_progress",
            "html_url": "https://example/run/1",
        },
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
        raise subprocess.CalledProcessError(
            returncode=2, cmd=["git", "rev-parse", "HEAD"]
        )

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


def test_ensure_release_tag_rejects_head_version_mismatch(
    monkeypatch, tmp_path: Path
) -> None:
    _init_release_repo(tmp_path, "1.2.2")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline.release_uploader, "_push_tag", lambda _tag: None)

    release = SimpleNamespace(version="1.2.3")

    with pytest.raises(RuntimeError, match="HEAD VERSION is 1.2.2, expected 1.2.3"):
        pipeline._ensure_release_tag(release, tmp_path / "publish.log")


def test_ensure_release_tag_rejects_existing_tag_version_mismatch(
    monkeypatch, tmp_path: Path
) -> None:
    _init_release_repo(tmp_path, "1.2.2")
    _run_git(tmp_path, "tag", "-a", "v1.2.3", "-m", "Release v1.2.3")
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    _run_git(tmp_path, "add", "VERSION")
    _run_git(tmp_path, "commit", "-m", "version 1.2.3")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline.release_uploader, "_push_tag", lambda _tag: None)

    release = SimpleNamespace(version="1.2.3")

    with pytest.raises(RuntimeError, match="v1.2.3 VERSION is 1.2.2"):
        pipeline._ensure_release_tag(release, tmp_path / "publish.log")


def test_ensure_release_tag_uses_git_adapter_for_tag_creation(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeGitAdapter:
        def __init__(self) -> None:
            self.calls: list[tuple[list[str], bool]] = []

        def run(self, args, *, check=True, input_text=None, timeout=None):
            self.calls.append((list(args), check))
            stdout = ""
            returncode = 0
            if args[:2] == ["git", "show"]:
                stdout = "1.2.3\n"
            elif args[:4] == ["git", "rev-parse", "--verify", "-q"]:
                returncode = 1
            return subprocess.CompletedProcess(
                args,
                returncode,
                stdout=stdout,
                stderr="",
            )

    adapter = FakeGitAdapter()
    monkeypatch.setattr(pipeline, "GIT_ADAPTER", adapter)
    monkeypatch.setattr(pipeline.release_uploader, "_push_tag", lambda _tag: None)

    tag_name = pipeline._ensure_release_tag(
        SimpleNamespace(version="1.2.3"), tmp_path / "publish.log"
    )

    assert tag_name == "v1.2.3"
    assert (
        ["git", "tag", "-a", "v1.2.3", "-m", "Release v1.2.3"],
        True,
    ) in adapter.calls


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
    monkeypatch.setattr(
        pipeline, "_get_release_or_response", lambda *_args: (DummyRelease(), None)
    )
    monkeypatch.setattr(
        pipeline, "_resolve_release_log_dir", lambda _path: (tmp_path, None)
    )
    monkeypatch.setattr(
        pipeline, "_handle_release_sync", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        pipeline, "_handle_release_restart", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        pipeline,
        "_prepare_logging",
        lambda ctx, *_args, **_kwargs: (ctx, tmp_path / "publish.log", ctx["step"]),
    )
    monkeypatch.setattr(
        pipeline, "_build_artifacts_stale", lambda *_args, **_kwargs: False
    )
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
    monkeypatch.setattr(
        pipeline, "_resolve_release_log_display", lambda *_args, **_kwargs: (False, "")
    )
    monkeypatch.setattr(pipeline, "_resolve_next_step", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "_build_release_step_states", lambda **_kwargs: [])
    monkeypatch.setattr(pipeline, "_get_user_github_token", lambda _user: None)
    monkeypatch.setattr(
        pipeline, "_resolve_github_token", lambda *_args, **_kwargs: "token"
    )
    monkeypatch.setattr(pipeline, "build_release_guidance", lambda **_kwargs: {})
    monkeypatch.setattr(
        pipeline, "_build_release_progress_context", lambda **_kwargs: {}
    )
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


def test_publish_step_compatibility_resets_inflight_session():
    typed_ctx = ReleasePublishContext(
        step=3,
        started=True,
        paused=True,
        extras={"publish_steps_schema": "old-step-order"},
    )

    result = pipeline._ensure_publish_step_compatibility(
        typed_ctx, pipeline.PUBLISH_STEPS
    )

    assert result.step == 0
    assert result.started is False
    assert result.paused is False
    assert result.error == (
        "Release publish steps changed after an upgrade. Restart the publish workflow to continue safely."
    )
    assert result.extras["publish_steps_schema"] == "|".join(
        name for name, _func in pipeline.PUBLISH_STEPS
    )


def test_publish_step_compatibility_records_schema_for_new_session():
    typed_ctx = ReleasePublishContext(step=0, started=False, paused=False, extras={})

    result = pipeline._ensure_publish_step_compatibility(
        typed_ctx, pipeline.PUBLISH_STEPS
    )

    assert result.step == 0
    assert result.started is False
    assert result.error is None
    assert result.extras["publish_steps_schema"] == "|".join(
        name for name, _func in pipeline.PUBLISH_STEPS
    )


def test_resolve_safe_child_path_rejects_parent_traversal(tmp_path: Path):
    with pytest.raises(ValueError):
        pipeline._resolve_safe_child_path(tmp_path, "../escape.txt")


def test_release_progress_returns_400_for_invalid_state_path(monkeypatch):
    class DummyRelease:
        pk = 1

    def raise_unsafe_path(*_args, **_kwargs):
        raise ValueError("unsafe")

    monkeypatch.setattr(
        pipeline, "_get_release_or_response", lambda *_args: (DummyRelease(), None)
    )
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


def test_reset_release_progress_redirects_to_canonical_release_route(tmp_path: Path):
    class DummyPackage:
        name = "arthexis"

    class DummyRelease:
        pk = 7
        package = DummyPackage()
        version = "1.2.3"
        pypi_url = "https://pypi.org/project/arthexis/1.2.3/"
        release_on = object()
        saved_fields: list[str] | None = None

        def save(self, *, update_fields):
            self.saved_fields = list(update_fields)

    release = DummyRelease()
    request = RequestFactory().get(
        "/admin/core/releases/7/publish/?next=https://evil.example"
    )
    request.session = {"release_publish_7": {"step": 3}}
    lock_path = tmp_path / "release.lock"
    restart_path = tmp_path / "release.restarts"
    lock_path.write_text("locked", encoding="utf-8")

    response = pipeline._reset_release_progress(
        request,
        release,
        "release_publish_7",
        lock_path,
        restart_path,
        tmp_path,
        clean_repo=False,
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("release-progress", args=[7, "publish"])
    assert "release_publish_7" not in request.session
    assert release.saved_fields == ["pypi_url", "release_on"]


def test_step_run_tests_accepts_recorded_successful_test_evidence(tmp_path: Path):
    ctx = {
        "tests_verified_at": "2026-04-10T00:00:00+00:00",
        "tests_command": "python manage.py test run -- --all",
        "tests_result": {"success": True},
    }

    pipeline._step_run_tests(object(), ctx, tmp_path / "publish.log")

    assert ctx["tests_result"]["success"] is True


def test_step_run_tests_requires_evidence_or_configured_command(
    monkeypatch, settings, tmp_path: Path
):
    ctx: dict[str, object] = {}
    settings.RELEASE_PUBLISH_VALIDATION_COMMAND = ""
    monkeypatch.setattr(
        pipeline,
        "_append_log",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(PublishPending):
        pipeline._step_run_tests(object(), ctx, tmp_path / "publish.log")

    assert "tests_verified_at" in ctx["error"]


def test_step_run_tests_executes_configured_validation_command(
    monkeypatch, settings, tmp_path: Path
):
    ctx: dict[str, object] = {}
    settings.RELEASE_PUBLISH_VALIDATION_COMMAND = "echo 'release tests ok'"

    class Completed:
        returncode = 0
        stdout = "release tests ok\n"
        stderr = ""

    monkeypatch.setattr(
        pipeline.subprocess,
        "run",
        lambda *_args, **_kwargs: Completed(),
    )
    monkeypatch.setattr(
        pipeline,
        "_append_log",
        lambda *_args, **_kwargs: None,
    )

    pipeline._step_run_tests(object(), ctx, tmp_path / "publish.log")

    assert ctx["tests_result"]["success"] is True
    assert ctx["tests_result"]["source"] == "pipeline_command"
    assert ctx["tests_command"] == "echo 'release tests ok'"
    assert "tests_verified_at" in ctx


def test_step_run_tests_passes_configured_timeout_to_subprocess_run(
    monkeypatch, settings, tmp_path: Path
):
    ctx: dict[str, object] = {}
    settings.RELEASE_PUBLISH_VALIDATION_COMMAND = "echo release tests ok"
    settings.RELEASE_PUBLISH_VALIDATION_TIMEOUT_SECONDS = 42
    call: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        call["command"] = command
        call["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    monkeypatch.setattr(
        pipeline,
        "_append_log",
        lambda *_args, **_kwargs: None,
    )

    pipeline._step_run_tests(object(), ctx, tmp_path / "publish.log")

    assert call["command"] == ["echo", "release", "tests", "ok"]
    assert call["kwargs"]["timeout"] == 42
    assert ctx["tests_result"]["success"] is True


def test_step_run_tests_records_timeout_result_and_logs_gate_failure(
    monkeypatch, settings, tmp_path: Path
):
    ctx: dict[str, object] = {}
    settings.RELEASE_PUBLISH_VALIDATION_COMMAND = "echo timeout"
    settings.RELEASE_PUBLISH_VALIDATION_TIMEOUT_SECONDS = 15
    logged_messages: list[str] = []

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs["timeout"])

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    monkeypatch.setattr(
        pipeline,
        "_append_log",
        lambda _path, message: logged_messages.append(message),
    )

    with pytest.raises(PublishPending):
        pipeline._step_run_tests(object(), ctx, tmp_path / "publish.log")

    assert ctx["paused"] is True
    assert ctx["tests_result"] == {
        "success": False,
        "reason": "timeout",
        "source": "pipeline_command",
        "timeout_seconds": 15,
    }
    assert "echo timeout" in ctx["error"]
    assert "15 seconds" in ctx["error"]
    assert any("timeout=15s" in message for message in logged_messages)
    assert any("timed out after 15 seconds" in message for message in logged_messages)


def test_step_confirm_pypi_trusted_publisher_settings_validates_expected_workflow_metadata(
    monkeypatch, tmp_path: Path
):
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "publish.yml").write_text(
        'on:\n  push:\n    tags:\n      - "v*"\n'
        "jobs:\n  publish-to-pypi:\n    permissions:\n      id-token: write\n"
        "    environment:\n      name: pypi\n"
        "    steps:\n      - uses: pypa/gh-action-pypi-publish@release/v1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        pipeline,
        "_append_log",
        lambda *_args, **_kwargs: None,
    )

    ctx: dict[str, object] = {}
    pipeline._step_confirm_pypi_trusted_publisher_settings(
        object(), ctx, tmp_path / "publish.log"
    )

    assert ctx["trusted_publisher_workflow_file"] == "publish.yml"
    assert ctx["trusted_publisher_ref"] == "refs/tags/v*"
    assert ctx["trusted_publisher_environment"] == "pypi"
    assert "trusted_publisher_verified_at" in ctx


def test_publish_workflow_uses_same_artifact_for_github_release_and_pypi() -> None:
    jobs = _publish_workflow_jobs()

    build_job = jobs["build"]
    release_job = jobs["publish-to-github-release"]
    pypi_job = jobs["publish-to-pypi"]

    assert release_job["needs"] == "build"
    assert release_job["permissions"] == {"contents": "write"}
    assert release_job["env"]["GH_REPO"] == "${{ github.repository }}"
    assert pypi_job["needs"] == ["build", "publish-to-github-release"]
    assert pypi_job["permissions"]["id-token"] == "write"
    assert pypi_job["permissions"]["contents"] == "read"

    build_upload = _workflow_step(build_job, "Upload dist artifacts")
    release_download = _workflow_step(release_job, "Download build artifacts")
    pypi_download = _workflow_step(pypi_job, "Download build artifacts")
    assert build_upload["with"]["name"] == "arthexis-dists"
    assert release_download["with"] == {"name": "arthexis-dists", "path": "dist/"}
    assert pypi_download["with"] == {"name": "arthexis-dists", "path": "dist/"}

    release_run = _workflow_step(
        release_job, "Upload distributions to GitHub Release"
    )["run"]
    assert "gh release create" in release_run
    assert "gh release upload" in release_run
    assert '--repo "${GITHUB_REPOSITORY}"' in release_run
    assert "dist/*.whl dist/*.tar.gz" in release_run
    assert "--notes-file" in release_run
    assert "--generate-notes" not in release_run
    assert "https://pypi.org/project/arthexis/%s/" in release_run


def test_tag_from_version_workflow_creates_release_tag_and_dispatches_publish() -> None:
    workflow = _workflow_data("tag-from-version.yml")
    on_section = _workflow_on(workflow)

    assert on_section["push"]["branches"] == ["main"]
    assert workflow["permissions"]["contents"] == "write"
    assert workflow["permissions"]["actions"] == "write"
    assert workflow["concurrency"]["cancel-in-progress"] is False

    job = workflow["jobs"]["tag-from-version"]

    create_tag_run = _workflow_step(job, "Create tag when missing")["run"]
    assert 'tag="v${VERSION}"' in create_tag_run
    assert 'git tag -a "$tag" -m "Release ${tag}"' in create_tag_run
    assert 'git push origin "$tag"' in create_tag_run

    dispatch_step = _workflow_step(job, "Dispatch publish workflow for created tag")
    assert dispatch_step["if"] == "steps.create_tag.outputs.created == 'true'"
    dispatch_run = dispatch_step["run"]
    assert 'tag="v${VERSION}"' in dispatch_run
    assert 'gh workflow run publish.yml --ref "$tag" -f release_tag="$tag"' in dispatch_run


def test_install_health_workflow_runs_on_default_branch_push_not_schedule() -> None:
    workflow = _workflow_data("install-health.yml")
    on_section = _workflow_on(workflow)

    assert "schedule" not in on_section
    assert on_section["push"] == {}
    assert "workflow_dispatch" in on_section

    install_job = workflow["jobs"]["install"]
    assert (
        "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
        in install_job["if"]
    )
    upload_step = _workflow_step(install_job, "Upload pytest log")
    assert upload_step["with"]["name"].startswith("install-health-pytest-results-")

    notify_recovery = workflow["jobs"]["notify_recovery"]
    assert "github.event_name == 'schedule'" not in notify_recovery["if"]
    assert (
        "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
        in notify_recovery["if"]
    )

    notify_failure = workflow["jobs"]["notify_failure"]
    assert (
        "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
        in notify_failure["if"]
    )


def test_release_simulator_requires_current_main_install_health_success() -> None:
    workflow = _workflow_data("release-simulator.yml")
    evaluate_job = workflow["jobs"]["evaluate"]
    evaluate_step = _workflow_step(
        evaluate_job, "Evaluate release blockers from install/upgrade pipeline state"
    )
    script = evaluate_step["with"]["script"]

    assert "github.rest.repos.getBranch" in script
    assert "defaultBranchSha" in script
    assert "const ciRuns = await github.paginate(github.rest.actions.listWorkflowRunsForRepo" in script
    assert "run.name === 'Install Health Check'" in script
    assert "run.head_sha === defaultBranchSha" in script
    assert "latestInstallHealthRun.conclusion !== 'success'" in script
    assert "Install Health Check has not run for current" in script


def test_release_simulator_requires_security_scan_settling_and_clear_alerts() -> None:
    workflow = _workflow_data("release-simulator.yml")
    evaluate_job = workflow["jobs"]["evaluate"]
    evaluate_step = _workflow_step(
        evaluate_job, "Evaluate release blockers from install/upgrade pipeline state"
    )
    script = evaluate_step["with"]["script"]

    assert evaluate_job["permissions"]["security-events"] == "read"
    assert "securityScanQuietMillis = 2 * 60 * 60 * 1000" in script
    assert "run.event === 'push'" in script
    assert "defaultBranchAdvancedAt" in script
    assert "Security scan settling period has not elapsed since" in script
    assert "Unable to verify when" in script
    assert "github.rest.codeScanning.listAlertsForRepo" in script
    assert "state: 'open'" in script
    assert "Open GitHub code scanning security findings" in script
    assert "Unable to verify GitHub code scanning alerts" in script


def test_release_simulator_pr_and_issue_blockers_have_required_permissions() -> None:
    workflow = _workflow_data("release-simulator.yml")
    evaluate_job = workflow["jobs"]["evaluate"]
    evaluate_step = _workflow_step(
        evaluate_job, "Evaluate release blockers from install/upgrade pipeline state"
    )
    script = evaluate_step["with"]["script"]

    assert evaluate_job["permissions"]["pull-requests"] == "read"
    assert "github.rest.pulls.list" in script
    assert "releaseReadinessReportTitle = 'Release Readiness Report'" in script
    assert "releaseReadinessReportMarker = '<!-- release-readiness-report -->'" in script
    assert "issue.title === releaseReadinessReportTitle" in script
    assert "issue.body?.includes(releaseReadinessReportMarker)" in script


@pytest.mark.django_db
def test_step_record_publish_metadata_records_github_release_url(
    monkeypatch, tmp_path: Path
):
    package = Package.objects.create(
        name="arthexis",
        repository_url="https://github.com/arthexis/arthexis",
    )
    release = PackageRelease.objects.create(package=package, version="1.2.3")

    monkeypatch.setattr(pipeline, "_pypi_release_available", lambda _release: True)
    monkeypatch.setattr(pipeline.PackageRelease, "dump_fixture", lambda: None)
    monkeypatch.setattr(
        pipeline,
        "_record_release_fixture_updates",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(pipeline, "_append_log", lambda *_args, **_kwargs: None)

    pipeline._step_record_publish_metadata(release, {}, tmp_path / "publish.log")

    release.refresh_from_db()
    assert release.pypi_url == "https://pypi.org/project/arthexis/1.2.3/"
    assert release.github_url == (
        "https://github.com/arthexis/arthexis/releases/tag/v1.2.3"
    )


@pytest.mark.django_db
def test_step_record_publish_metadata_uses_github_target_url(
    monkeypatch, tmp_path: Path
):
    package = Package.objects.create(
        name="widget",
        repository_url="https://example.com/acme/widget",
    )
    release = PackageRelease.objects.create(package=package, version="2.3.4")

    monkeypatch.setattr(pipeline, "_pypi_release_available", lambda _release: True)
    monkeypatch.setattr(pipeline.PackageRelease, "dump_fixture", lambda: None)
    monkeypatch.setattr(
        release,
        "build_publish_targets",
        lambda: [
            RepositoryTarget(name="PyPI"),
            RepositoryTarget(
                name="GitHub Release",
                repository_url="git@github.com:acme/widget.git",
            ),
        ],
    )
    monkeypatch.setattr(
        pipeline,
        "_record_release_fixture_updates",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(pipeline, "_append_log", lambda *_args, **_kwargs: None)

    pipeline._step_record_publish_metadata(release, {}, tmp_path / "publish.log")

    release.refresh_from_db()
    assert release.github_url == "https://github.com/acme/widget/releases/tag/v2.3.4"


def test_step_confirm_pypi_trusted_publisher_settings_accepts_yaml_variants(
    monkeypatch, tmp_path: Path
):
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "publish.yml").write_text(
        "on:\n  push:\n    tags: ['v*']\n"
        "jobs:\n  publish-to-pypi:\n    permissions:\n      id-token: write\n"
        "    environment: pypi\n"
        "    steps:\n      - uses: pypa/gh-action-pypi-publish\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        pipeline,
        "_append_log",
        lambda *_args, **_kwargs: None,
    )

    ctx: dict[str, object] = {}
    pipeline._step_confirm_pypi_trusted_publisher_settings(
        object(), ctx, tmp_path / "publish.log"
    )

    assert ctx["trusted_publisher_ref"] == "refs/tags/v*"
    assert ctx["trusted_publisher_environment"] == "pypi"


def test_step_prune_low_value_tests_pauses_for_operator_evidence(
    monkeypatch, settings, tmp_path: Path
):
    settings.RELEASE_PUBLISH_TEST_PRUNING_PR_URL = ""
    monkeypatch.setattr(pipeline, "_append_log", lambda *_args, **_kwargs: None)
    ctx: dict[str, object] = {}

    with pytest.raises(PublishPending):
        pipeline._step_prune_low_value_tests(object(), ctx, tmp_path / "publish.log")

    assert ctx["paused"] is True
    assert ctx["test_pruning_required"] is True
    assert "worst 1% of tests" in ctx["test_pruning_error"]
    assert "error" not in ctx


def test_step_prune_low_value_tests_accepts_scheduled_setting(
    monkeypatch, settings, tmp_path: Path
):
    settings.RELEASE_PUBLISH_TEST_PRUNING_PR_URL = (
        "https://github.com/arthexis/arthexis/pull/7000"
    )
    monkeypatch.setattr(pipeline, "_append_log", lambda *_args, **_kwargs: None)
    ctx: dict[str, object] = {"auto_release": True}

    pipeline._step_prune_low_value_tests(object(), ctx, tmp_path / "publish.log")

    assert (
        ctx["test_pruning_pr_url"] == "https://github.com/arthexis/arthexis/pull/7000"
    )
    assert ctx["test_pruning_result"] == {
        "success": True,
        "source": "settings",
        "pr_url": "https://github.com/arthexis/arthexis/pull/7000",
        "criteria": list(pipeline.TEST_PRUNING_CRITERIA),
    }


def test_step_prune_low_value_tests_ignores_setting_for_interactive_release(
    monkeypatch, settings, tmp_path: Path
):
    settings.RELEASE_PUBLISH_TEST_PRUNING_PR_URL = (
        "https://github.com/arthexis/arthexis/pull/7000"
    )
    monkeypatch.setattr(pipeline, "_append_log", lambda *_args, **_kwargs: None)
    ctx: dict[str, object] = {}

    with pytest.raises(PublishPending):
        pipeline._step_prune_low_value_tests(object(), ctx, tmp_path / "publish.log")

    assert ctx["test_pruning_required"] is True
    assert "test_pruning_result" not in ctx
    assert "test_pruning_pr_url" not in ctx


def test_step_prune_low_value_tests_rejects_invalid_scheduled_setting(
    monkeypatch, settings, tmp_path: Path
):
    settings.RELEASE_PUBLISH_TEST_PRUNING_PR_URL = (
        "https://github.com/arthexis/arthexis/issues/7000"
    )
    monkeypatch.setattr(pipeline, "_append_log", lambda *_args, **_kwargs: None)
    ctx: dict[str, object] = {"auto_release": True}

    with pytest.raises(PublishPending):
        pipeline._step_prune_low_value_tests(object(), ctx, tmp_path / "publish.log")

    assert "GitHub pull request URL" in ctx["error"]
    assert "test_pruning_result" not in ctx


def test_step_prune_low_value_tests_rejects_invalid_prepopulated_url(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(pipeline, "_append_log", lambda *_args, **_kwargs: None)
    ctx: dict[str, object] = {
        "test_pruning_pr_url": "https://example.com/arthexis/arthexis/pull/7000"
    }

    with pytest.raises(PublishPending):
        pipeline._step_prune_low_value_tests(object(), ctx, tmp_path / "publish.log")

    assert "GitHub pull request URL" in ctx["error"]
    assert "test_pruning_result" not in ctx


def test_step_prune_low_value_tests_rejects_explicit_failure(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(pipeline, "_append_log", lambda *_args, **_kwargs: None)
    ctx = {
        "test_pruning_result": {"success": False},
        "test_pruning_pr_url": "https://github.com/arthexis/arthexis/pull/7000",
    }

    with pytest.raises(PublishPending):
        pipeline._step_prune_low_value_tests(object(), ctx, tmp_path / "publish.log")

    assert "explicitly failed" in ctx["error"]


def test_publish_workflow_records_operator_test_pruning_evidence(
    monkeypatch, tmp_path: Path
):
    captured: dict[str, object] = {}
    request = RequestFactory().post(
        "/release/publish",
        {
            "set_test_pruning_evidence": "1",
            "test_pruning_pr_url": "https://github.com/arthexis/arthexis/pull/7000",
        },
    )
    request.user = type("User", (), {"is_authenticated": False})()
    request.session = {}
    monkeypatch.setattr(
        workflow_module.messages, "success", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        workflow_module,
        "persist_release_context",
        lambda _request, _session_key, ctx, _lock_path: captured.update(ctx),
    )

    workflow = workflow_module.ReleasePublishWorkflow(
        request=request,
        session_key="release_publish_1",
        lock_path=tmp_path / "release.lock",
        restart_path=tmp_path / "release.restarts",
        clean_redirect_path=lambda _request, path: path,
        collect_dirty_files=lambda: [],
        validate_manual_git_push=lambda _pending_push: True,
        append_log=lambda *_args, **_kwargs: None,
    )
    ctx = workflow_module.ReleasePublishContext(
        step=5,
        started=True,
        paused=True,
        extras={"test_pruning_required": True},
    )

    result, resume_requested, response = workflow.resume(ctx)

    assert resume_requested is False
    assert response.status_code == 302
    assert response["Location"] == "/release/publish?resume=1&step=5"
    assert result.paused is False
    assert result.extras["test_pruning_result"] == {
        "success": True,
        "source": "operator",
        "pr_url": "https://github.com/arthexis/arthexis/pull/7000",
    }
    assert (
        captured["test_pruning_pr_url"]
        == "https://github.com/arthexis/arthexis/pull/7000"
    )


def test_publish_workflow_rejects_invalid_test_pruning_url(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}
    request = RequestFactory().post(
        "/release/publish",
        {
            "set_test_pruning_evidence": "1",
            "test_pruning_pr_url": "https://github.com/arthexis/arthexis/issues/7000",
        },
    )
    request.user = type("User", (), {"is_authenticated": False})()
    request.session = {}
    monkeypatch.setattr(
        workflow_module.messages, "error", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        workflow_module,
        "store_release_context",
        lambda _request, _session_key, ctx: captured.update(ctx),
    )

    workflow = workflow_module.ReleasePublishWorkflow(
        request=request,
        session_key="release_publish_1",
        lock_path=tmp_path / "release.lock",
        restart_path=tmp_path / "release.restarts",
        clean_redirect_path=lambda _request, path: path,
        collect_dirty_files=lambda: [],
        validate_manual_git_push=lambda _pending_push: True,
        append_log=lambda *_args, **_kwargs: None,
    )
    ctx = workflow_module.ReleasePublishContext(
        step=5,
        started=True,
        paused=True,
        extras={"test_pruning_required": True},
    )

    result, resume_requested, response = workflow.resume(ctx)

    assert resume_requested is False
    assert response.status_code == 302
    assert response["Location"] == "/release/publish"
    assert result.paused is True
    assert result.extras["test_pruning_required"] is True
    assert "valid GitHub pull request URL" in result.extras["test_pruning_error"]
    assert "test_pruning_result" not in result.extras
    assert captured["test_pruning_required"] is True
    assert "test_pruning_result" not in captured


def test_step_confirm_pypi_trusted_publisher_settings_fails_on_mismatch(
    monkeypatch, tmp_path: Path
):
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "publish.yml").write_text(
        'on:\n  push:\n    tags:\n      - "release-*"\n'
        "jobs:\n  publish-to-pypi:\n    permissions:\n      id-token: write\n"
        "    environment:\n      name: production\n"
        "    steps:\n      - uses: pypa/gh-action-pypi-publish@release/v1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        pipeline,
        "_append_log",
        lambda *_args, **_kwargs: None,
    )
    ctx: dict[str, object] = {}

    with pytest.raises(PublishPending):
        pipeline._step_confirm_pypi_trusted_publisher_settings(
            object(), ctx, tmp_path / "publish.log"
        )

    assert "workflow tag pattern must be refs/tags/v*" in ctx["error"]
    assert "jobs.publish-to-pypi.environment.name" in ctx["error"]


def test_step_confirm_pypi_trusted_publisher_settings_rejects_mixed_tag_patterns(
    monkeypatch, tmp_path: Path
):
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "publish.yml").write_text(
        "on:\n  push:\n    tags: ['v*', 'release-*']\n"
        "jobs:\n  publish-to-pypi:\n    permissions:\n      id-token: write\n"
        "    environment:\n      name: pypi\n"
        "    steps:\n      - uses: pypa/gh-action-pypi-publish@release/v1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        pipeline,
        "_append_log",
        lambda *_args, **_kwargs: None,
    )
    ctx: dict[str, object] = {}

    with pytest.raises(PublishPending):
        pipeline._step_confirm_pypi_trusted_publisher_settings(
            object(), ctx, tmp_path / "publish.log"
        )

    assert "workflow tag pattern must be refs/tags/v*" in ctx["error"]


def test_step_confirm_pypi_trusted_publisher_settings_rejects_static_publish_tokens(
    monkeypatch, tmp_path: Path
):
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "publish.yml").write_text(
        'on:\n  push:\n    tags:\n      - "v*"\n'
        "jobs:\n  publish-to-pypi:\n"
        "    permissions:\n      id-token: write\n"
        "    environment:\n      name: pypi\n"
        "    steps:\n"
        "      - uses: pypa/gh-action-pypi-publish@release/v1\n"
        "        with:\n"
        "          password: ${{ secrets.PYPI_API_TOKEN }}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        pipeline,
        "_append_log",
        lambda *_args, **_kwargs: None,
    )
    ctx: dict[str, object] = {}

    with pytest.raises(PublishPending):
        pipeline._step_confirm_pypi_trusted_publisher_settings(
            object(), ctx, tmp_path / "publish.log"
        )

    assert "must not set static token credentials" in ctx["error"]


def test_step_confirm_pypi_trusted_publisher_settings_allows_non_publish_step_tokens(
    monkeypatch, tmp_path: Path
):
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "publish.yml").write_text(
        'on:\n  push:\n    tags:\n      - "v*"\n'
        "jobs:\n  publish-to-pypi:\n"
        "    permissions:\n      id-token: write\n"
        "    environment:\n      name: pypi\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "        with:\n"
        "          token: ${{ secrets.GITHUB_TOKEN }}\n"
        "      - uses: pypa/gh-action-pypi-publish@release/v1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        pipeline,
        "_append_log",
        lambda *_args, **_kwargs: None,
    )
    ctx: dict[str, object] = {}

    pipeline._step_confirm_pypi_trusted_publisher_settings(
        object(), ctx, tmp_path / "publish.log"
    )

    assert "trusted_publisher_verified_at" in ctx

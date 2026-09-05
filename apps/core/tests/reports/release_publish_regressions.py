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
from apps.release.publishing.pipeline import pypi as pypi_checks


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


def _workflow_files() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[4]
    return sorted((repo_root / ".github" / "workflows").glob("*.yml"))


def _workflow_on(workflow: dict[str, object]) -> object:
    return workflow.get("on", workflow.get(True, {}))


def _workflow_step(job: dict[str, object], name: str) -> dict[str, object]:
    return next(step for step in job["steps"] if step.get("name") == name)


def _walk_values(value: object):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


def _workflow_path_triggers(on_section: object) -> list[str]:
    paths: list[str] = []
    if not isinstance(on_section, dict):
        return paths
    for event in on_section.values():
        if isinstance(event, dict):
            event_paths = event.get("paths", [])
            if isinstance(event_paths, list):
                paths.extend(str(path) for path in event_paths)
    return paths


def test_github_workflows_do_not_define_windows_gates() -> None:
    forbidden_workflow_tokens = [
        "install-windows-smoke",
        "msys2/setup-msys2",
    ]

    for workflow_path in _workflow_files():
        workflow = (
            pipeline.yaml.safe_load(workflow_path.read_text(encoding="utf-8")) or {}
        )

        for job_name, job in workflow.get("jobs", {}).items():
            runs_on = job.get("runs-on") if isinstance(job, dict) else None
            runner_labels = runs_on if isinstance(runs_on, list) else [runs_on]
            assert not [
                label
                for label in runner_labels
                if isinstance(label, str) and "windows" in label.lower()
            ], f"{workflow_path.name}:{job_name} uses a Windows runner"

        workflow_values = [str(value) for value in _walk_values(workflow)]
        for token in forbidden_workflow_tokens:
            assert not any(
                token in value for value in workflow_values
            ), f"{workflow_path.name} references {token}"

        windows_path_triggers = [
            path
            for path in _workflow_path_triggers(_workflow_on(workflow))
            if ".bat" in path.lower()
        ]
        assert not windows_path_triggers, (
            f"{workflow_path.name} gates Windows batch changes: "
            f"{windows_path_triggers}"
        )


def test_linux_ci_and_security_scans_run_on_pull_requests() -> None:
    pr_workflows: list[str] = []
    for workflow_path in _workflow_files():
        workflow = (
            pipeline.yaml.safe_load(workflow_path.read_text(encoding="utf-8")) or {}
        )
        on_section = _workflow_on(workflow)
        if isinstance(on_section, dict) and (
            "pull_request" in on_section or "pull_request_target" in on_section
        ):
            pr_workflows.append(workflow_path.name)

    assert pr_workflows == [
        "ci.yml",
        "codeql.yml",
        "secret-scan.yml",
    ]


def test_security_workflows_do_not_keep_dormant_pr_conditions() -> None:
    for workflow_name in ("codeql.yml", "security-scan.yml"):
        workflow_text = (Path(".github") / "workflows" / workflow_name).read_text(
            encoding="utf-8"
        )

        assert "github.event_name == 'pull_request'" not in workflow_text
        assert "context.payload.pull_request" not in workflow_text
        assert "pull-requests: write" not in workflow_text


def test_security_workflows_keep_scheduled_baseline_scans() -> None:
    scheduled_workflows: list[str] = []
    for workflow_path in _workflow_files():
        workflow = (
            pipeline.yaml.safe_load(workflow_path.read_text(encoding="utf-8")) or {}
        )
        on_section = _workflow_on(workflow)
        if isinstance(on_section, dict) and "schedule" in on_section:
            scheduled_workflows.append(workflow_path.name)

    assert scheduled_workflows == [
        "codeql.yml",
        "secret-scan.yml",
        "security-scan.yml",
    ]


def test_zap_baseline_scan_stays_schedule_or_manual_only() -> None:
    workflow = _workflow_data("security-scan.yml")
    on_section = _workflow_on(workflow)
    zap_job = workflow["jobs"]["zap-baseline"]

    assert "pull_request" not in on_section
    assert "schedule" in on_section
    assert "workflow_dispatch" in on_section
    assert "if" not in zap_job


def test_scheduled_secret_scan_uses_non_empty_history_range() -> None:
    workflow = _workflow_data("secret-scan.yml")
    gitleaks_job = workflow["jobs"]["gitleaks"]
    scan_step = _workflow_step(gitleaks_job, "Run working tree and commit range scans")
    script = scan_step["run"]

    assert 'log_range="--all"' in script
    assert 'log_range="origin/main..HEAD"' not in script


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


def test_pypi_release_available_treats_invalid_json_as_unavailable():
    class InvalidJsonResponse:
        ok = True

        def __init__(self) -> None:
            self.closed = False

        def json(self):
            raise ValueError("invalid JSON")

        def close(self) -> None:
            self.closed = True

    response = InvalidJsonResponse()
    release = SimpleNamespace(
        package=SimpleNamespace(name="arthexis"),
        version="1.2.3",
    )

    available = pypi_checks.pypi_release_available(
        release,
        network_available=lambda: True,
        request_timeout=1,
        requests_get=lambda *_args, **_kwargs: response,
    )

    assert available is False
    assert response.closed is True


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


def test_ensure_release_tag_falls_back_to_lightweight_without_git_identity(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeGitAdapter:
        def __init__(self) -> None:
            self.calls: list[tuple[list[str], bool]] = []

        def run(self, args, *, check=True, input_text=None, timeout=None):
            self.calls.append((list(args), check))
            if args[:5] == ["git", "tag", "-a", "v1.2.3", "-m"]:
                raise subprocess.CalledProcessError(
                    returncode=128,
                    cmd=args,
                    stderr="Committer identity unknown",
                )
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
    assert (["git", "tag", "v1.2.3"], True) in adapter.calls


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
        "tests_command": "python -m pytest",
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

    readiness_job = jobs["readiness-gate"]
    build_job = jobs["build"]
    release_job = jobs["publish-to-github-release"]
    pypi_job = jobs["publish-to-pypi"]

    assert readiness_job["permissions"] == {
        "actions": "write",
        "contents": "read",
        "issues": "read",
    }
    assert jobs["test"]["needs"] == "readiness-gate"
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

    release_run = _workflow_step(release_job, "Upload distributions to GitHub Release")[
        "run"
    ]
    assert "gh release create" in release_run
    assert "gh release upload" in release_run
    assert '--repo "${GITHUB_REPOSITORY}"' in release_run
    assert "dist/*.whl dist/*.tar.gz" in release_run
    assert "gh release edit" in release_run
    assert "--notes-file" in release_run
    assert "--generate-notes" not in release_run
    assert 'release_title="Release ${release_version}"' in release_run
    assert '--title "${release_title}"' in release_run
    assert "Arthexis has a new release: %s." in release_run
    assert "standard release channel." in release_run
    assert "[View the package on PyPI]" in release_run
    assert "https://pypi.org/project/arthexis/%s/" in release_run
    assert release_run.count("https://pypi.org/project/arthexis/%s/") == 1
    assert "Automated Arthexis" not in release_run
    assert "## Artifacts" not in release_run
    assert "Source tag" not in release_run
    assert "Generated by" not in release_run


def test_publish_workflow_blocks_until_readiness_report_is_green() -> None:
    readiness_job = _publish_workflow_jobs()["readiness-gate"]
    gate_step = _workflow_step(readiness_job, "Verify release readiness report")
    gate_run = gate_step["run"]

    assert gate_step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert gate_step["env"]["RELEASE_TAG"] == "${{ env.RELEASE_TAG }}"
    assert "Release Readiness Report in:title" in gate_run
    assert "<!-- release-readiness-report -->" in gate_run
    assert "<!-- release-readiness-fingerprint:" in gate_run
    assert "gh workflow run release-simulator.yml" in gate_run
    assert 'release_simulator_ref="refs/heads/${DEFAULT_BRANCH}"' in gate_run
    assert '--ref "${release_simulator_ref}"' in gate_run
    assert "Release readiness report is not green" in gate_run
    assert "RELEASE_READINESS_TIMEOUT_SECONDS" in gate_run
    assert 'expected_commit_line="- Commit: ${target_sha}"' in gate_run
    assert '[[ "$report_body" != *"$expected_commit_line"* ]]' in gate_run
    assert (
        "expected_simulated_version='simulated VERSION `'\"${version}\"'`'" in gate_run
    )
    assert '[[ "$report_body" != *"$expected_simulated_version"* ]]' in gate_run
    assert "if ! REPORT_BODY=\"$report_body\" python - <<'PY'" in gate_run
    assert 'blockers_lines != ["- None detected."]' in gate_run
    assert "recommendation_lines != [" in gate_run
    assert (
        "✅ Recommendation: release can proceed once maintainers approve authorization."
        in gate_run
    )
    assert "Release publish blocked" in gate_run


def test_publish_workflow_leaves_abandoned_release_pr_cleanup_to_readiness() -> None:
    assert "close-superseded-release-prs" not in _publish_workflow_jobs()


def test_install_health_workflow_is_manual_only_not_scheduled() -> None:
    workflow = _workflow_data("install-health.yml")
    on_section = _workflow_on(workflow)

    assert "pull_request" not in on_section
    assert "push" not in on_section
    assert "schedule" not in on_section
    assert "workflow_dispatch" in on_section

    install_job = workflow["jobs"]["install"]
    assert install_job["if"] == "${{ github.event_name == 'workflow_dispatch' }}"
    assert "container" not in install_job
    assert "services" not in install_job
    assert install_job["env"]["OCPP_STATE_REDIS_URL"] == "redis://localhost:6379"
    assert install_job["env"]["REDIS_HOST"] == "127.0.0.1"
    assert install_job["env"]["POSTGRES_HOST"] == "127.0.0.1"
    matrix_entries = install_job["strategy"]["matrix"]["include"]
    assert [
        (
            entry["os_flavor"],
            entry["python_version"],
            entry["db_backend"],
            entry["test_shard"],
            entry["pytest_args"],
            entry["full_pytest"],
        )
        for entry in matrix_entries
    ] == [
        ("ubuntu", "3.12", "sqlite", "smoke", "", False),
        ("ubuntu", "3.12", "postgres", "smoke", "", False),
        ("ubuntu", "3.11", "sqlite", "ocpp", "apps/ocpp/tests", True),
        ("ubuntu", "3.11", "sqlite", "rest", "--ignore=apps/ocpp/tests", True),
        ("ubuntu", "3.11", "postgres", "smoke", "", False),
    ]

    assert (
        install_job["name"]
        == "install (${{ matrix.os_flavor }}, py${{ matrix.python_version }}, ${{ matrix.db_backend }}, ${{ matrix.test_shard }})"
    )
    install_checkout_step = next(
        step
        for step in install_job["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert install_checkout_step["uses"].count("@") == 1
    assert "@v" not in install_checkout_step["uses"]
    assert install_checkout_step["with"]["persist-credentials"] is False
    assert (
        _workflow_step(install_job, "Start native Redis")["run"].strip()
        == "./scripts/ci/start-native-redis.sh"
    )
    start_postgres_step = _workflow_step(install_job, "Start native PostgreSQL")
    assert start_postgres_step["if"] == "${{ matrix.db_backend == 'postgres' }}"
    assert start_postgres_step["run"].strip() == "./scripts/ci/start-native-postgres.sh"

    detect_xdist_step = _workflow_step(install_job, "Detect pytest xdist arguments")
    assert detect_xdist_step["if"] == "${{ matrix.full_pytest }}"

    run_pytest_step = _workflow_step(install_job, "Run install pytest shard")
    run_pytest_script = run_pytest_step["run"]
    assert run_pytest_step["if"] == "${{ matrix.full_pytest }}"
    assert 'read -r -a target_args <<< "${{ matrix.pytest_args }}"' in run_pytest_script
    assert (
        'python -m pytest "${target_args[@]}" "${xdist_args[@]}"' in run_pytest_script
    )
    assert "--durations=25" in run_pytest_script

    upload_step = _workflow_step(install_job, "Upload pytest log")
    upload_name = upload_step["with"]["name"]
    assert upload_step["if"] == "${{ always() && matrix.full_pytest }}"
    assert (
        upload_name == "install-health-pytest-results-${{ matrix.os_flavor }}-"
        "${{ matrix.db_backend }}-${{ matrix.test_shard }}"
    )

    assert "pr_affected_linux_install" not in workflow["jobs"]
    assert "notify_failure" not in workflow["jobs"]
    assert "notify_recovery" not in workflow["jobs"]


@pytest.mark.parametrize(
    ("workflow_filename", "job_name"),
    [
        ("install-health.yml", "install"),
        ("publish.yml", "test"),
        ("release-upgrade-replay.yml", "replay"),
    ],
)
def test_host_redis_workflows_use_native_service(
    workflow_filename: str, job_name: str
) -> None:
    workflow = _workflow_data(workflow_filename)
    job = workflow["jobs"][job_name]
    env = {**workflow.get("env", {}), **job.get("env", {})}

    assert job["runs-on"] == "ubuntu-latest"
    assert "container" not in job
    assert "services" not in job
    assert env["OCPP_STATE_REDIS_URL"] == "redis://localhost:6379"
    assert (
        _workflow_step(job, "Start native Redis")["run"].strip()
        == "./scripts/ci/start-native-redis.sh"
    )


def test_linux_ci_uses_single_sanity_job() -> None:
    workflow = _workflow_data("ci.yml")
    on_section = _workflow_on(workflow)

    assert list(workflow["jobs"]) == ["sanity"]
    assert on_section["pull_request"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
    ]
    assert "workflow_dispatch" in on_section
    assert "env-refresh.bat" not in on_section["pull_request"]["paths"]
    assert "install.bat" not in on_section["pull_request"]["paths"]

    sanity_job = workflow["jobs"]["sanity"]
    assert sanity_job["name"] == "Linux sanity"
    assert workflow["env"]["ARTHEXIS_SKIP_SANITY_APT"] == "1"
    assert sanity_job["runs-on"] == [
        "self-hosted",
        "Linux",
        "X64",
        "arthexis-ci",
    ]
    assert sanity_job["timeout-minutes"] == 40
    checkout_step = next(
        step
        for step in sanity_job["steps"]
        if step.get("uses") == "actions/checkout@v6"
    )
    checkout_clean = (checkout_step.get("with") or {}).get("clean", True)
    assert checkout_clean is True or (
        isinstance(checkout_clean, str) and checkout_clean.lower() == "true"
    )
    clean_command = _workflow_step(sanity_job, "Clean workspace")["run"].strip()
    assert clean_command == "git clean -ffdx"
    assert ".venv" not in clean_command
    assert not any(
        isinstance(run := step.get("run"), str)
        and ".venv" in run
        and "git clean" in run
        for step in sanity_job["steps"]
    )
    assert (
        _workflow_step(sanity_job, "Run Linux sanity checks")["run"].strip()
        == "./scripts/ci/linux-sanity.sh"
    )


def test_linux_sanity_refreshes_cached_virtualenv_before_checks() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    script = (repo_root / "scripts/ci/linux-sanity.sh").read_text(encoding="utf-8")
    smoke_script = (repo_root / "scripts/ci/install-linux-smoke.sh").read_text(
        encoding="utf-8"
    )

    assert "./scripts/ci/install-linux-smoke.sh --cold" in script
    assert "./scripts/ci/install-linux-smoke.sh\n" in script
    assert script.index("./scripts/ci/install-linux-smoke.sh") < script.index(
        "source .venv/bin/activate"
    )
    assert "python manage.py check --fail-level ERROR" not in script
    assert 'DB_MODE="${ARTHEXIS_CI_INSTALL_SMOKE_DB_MODE:-graph}"' in smoke_script
    assert "./env-refresh.sh --deps-only" in smoke_script
    assert 'if [[ "$DB_MODE" == "apply" ]]; then' in smoke_script


def test_python_dependency_submission_has_runtime_version() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    python_version = (repo_root / ".python-version").read_text(encoding="utf-8").strip()
    workflow = _workflow_data("ci.yml")
    sanity_job = workflow["jobs"]["sanity"]
    setup_python_steps = [
        step
        for step in sanity_job["steps"]
        if str(step.get("uses", "")).startswith("actions/setup-python@")
    ]

    assert python_version == "3.13"
    assert (repo_root / "requirements.txt").is_file()
    assert ".python-version" in _workflow_path_triggers(_workflow_on(workflow))
    assert len(setup_python_steps) == 1
    assert setup_python_steps[0]["with"]["python-version"] == python_version


def test_release_simulator_refreshes_after_security_and_install_evidence() -> None:
    workflow = _workflow_data("release-simulator.yml")
    on_section = _workflow_on(workflow)
    evaluate_job = workflow["jobs"]["evaluate"]

    assert on_section == {"workflow_dispatch": None}
    assert "mark_outdated_on_default_branch_change" not in workflow["jobs"]
    assert "if" not in evaluate_job

    workflow_text = Path(".github/workflows/release-simulator.yml").read_text(
        encoding="utf-8"
    )
    assert "schedule:" not in workflow_text
    assert "workflow_run:" not in workflow_text
    assert "scheduled simulator" not in workflow_text
    assert "Runs hourly" not in workflow_text


def test_prepare_release_workflow_is_manual_only_and_trusted() -> None:
    workflow = _workflow_data("prepare-release.yml")
    on_section = _workflow_on(workflow)
    plan_job = workflow["jobs"]["prepare-release-plan"]
    write_job = workflow["jobs"]["open-or-update-release-pr"]

    assert workflow["permissions"] == {}
    assert "workflow_dispatch" in on_section
    assert "workflow_run" not in on_section
    assert "schedule" not in on_section
    assert plan_job["permissions"] == {
        "contents": "read",
        "issues": "read",
        "pull-requests": "read",
    }
    assert "if" not in plan_job
    assert plan_job["outputs"]["planned_sha"] == "${{ steps.source.outputs.sha }}"
    assert (
        plan_job["outputs"]["next_version"] == "${{ steps.plan.outputs.next_version }}"
    )
    assert "superseded_release_prs" not in plan_job["outputs"]

    assert all(step.get("uses") != "actions/checkout@v6" for step in plan_job["steps"])
    fetch_step = _workflow_step(plan_job, "Fetch trusted default branch")
    assert (
        fetch_step["env"]["DEFAULT_BRANCH"]
        == "${{ github.event.repository.default_branch }}"
    )
    assert "x-access-token:%s" in fetch_step["run"]
    assert (
        "http.https://github.com/.extraheader=AUTHORIZATION: basic" in fetch_step["run"]
    )
    assert "AUTHORIZATION: bearer" not in fetch_step["run"]
    assert "refs/remotes/origin/${DEFAULT_BRANCH}" in fetch_step["run"]
    assert "workflow_run.head_sha" not in str(fetch_step)

    source_step = _workflow_step(plan_job, "Record trusted source")
    assert "git rev-parse HEAD" in source_step["run"]
    assert 'echo "sha=$actual" >> "$GITHUB_OUTPUT"' in source_step["run"]
    assert "current=false" not in source_step["run"]

    readiness_step = _workflow_step(plan_job, "Confirm manual release preparation")
    assert 'echo "ready=true" >> "$GITHUB_OUTPUT"' in readiness_step["run"]
    assert "Release Readiness Report in:title" not in readiness_step["run"]
    assert "github.event.workflow_run" not in str(plan_job)

    assert all(
        step.get("name") != "Supersede halted patch release PRs"
        for step in plan_job["steps"]
    )

    assert write_job["permissions"] == {
        "contents": "write",
        "issues": "write",
        "pull-requests": "write",
    }
    assert all(step.get("uses") != "actions/checkout@v6" for step in write_job["steps"])
    push_step = _workflow_step(write_job, "Push branch and open or update PR")
    assert (
        push_step["env"]["GH_TOKEN"]
        == "${{ secrets.RELEASE_PREPARE_PR_TOKEN || github.token }}"
    )
    assert (
        push_step["env"]["PLANNED_SHA"]
        == "${{ needs.prepare-release-plan.outputs.planned_sha }}"
    )
    assert "SUPERSEDED_RELEASE_PRS" not in push_step["env"]
    assert 'if [ "$base_sha" != "$PLANNED_SHA" ]; then' in push_step["run"]
    assert "repos/${GITHUB_REPOSITORY}/contents/VERSION" in push_step["run"]
    assert "gh pr list \\" in push_step["run"]
    assert 'gh pr edit "$existing_pr" \\' in push_step["run"]
    assert "gh pr create \\" in push_step["run"]
    assert 'gh pr comment "$pr_number"' not in push_step["run"]
    assert 'gh pr close "$pr_number"' not in push_step["run"]
    assert "after a successful publish" not in push_step["run"]
    assert push_step["run"].count('--repo "${GITHUB_REPOSITORY}"') == 4

    workflow_text = Path(".github/workflows/prepare-release.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_run:" not in workflow_text
    assert "Automatic release prepare" not in workflow_text


def test_release_simulator_requires_current_main_install_health_success() -> None:
    workflow = _workflow_data("release-simulator.yml")
    evaluate_job = workflow["jobs"]["evaluate"]
    evaluate_step = _workflow_step(
        evaluate_job, "Evaluate release blockers from install/upgrade pipeline state"
    )
    script = evaluate_step["with"]["script"]

    assert "github.rest.repos.getBranch" in script
    assert "defaultBranchSha" in script
    assert (
        "const ciRuns = await github.paginate(github.rest.actions.listWorkflowRunsForRepo"
        in script
    )
    assert "run.name === 'Install Health Check'" in script
    assert "run.head_sha === defaultBranchSha" in script
    assert "latestInstallHealthRun.conclusion !== 'success'" in script
    assert "Install Health Check has not run for current" in script


def test_release_simulator_ignores_retired_install_health_issue_marker() -> None:
    workflow = _workflow_data("release-simulator.yml")
    evaluate_job = workflow["jobs"]["evaluate"]
    evaluate_step = _workflow_step(
        evaluate_job, "Evaluate release blockers from install/upgrade pipeline state"
    )
    script = evaluate_step["with"]["script"]

    assert "retiredInstallHealthIssueTitle" in script
    assert "retiredInstallHealthIssueMarker" in script
    assert "install-health-check-failure" in script
    assert "installIssueTitle" not in script
    assert "installMarker" not in script
    assert "installFailureIssue" not in script
    assert "Open install failure issue" not in script
    assert "issue.title === retiredInstallHealthIssueTitle" in script
    assert "issue.body?.includes(retiredInstallHealthIssueMarker)" in script


def test_release_upgrade_replay_workflow_replays_latest_release_to_candidate() -> None:
    workflow = _workflow_data("release-upgrade-replay.yml")
    on_section = _workflow_on(workflow)
    replay_job = workflow["jobs"]["replay"]

    assert workflow["env"]["ARTHEXIS_DB_BACKEND"] == "sqlite"
    assert "schedule" not in on_section
    assert on_section["workflow_dispatch"]["inputs"]["base_ref"]["required"] is False
    assert (
        on_section["workflow_dispatch"]["inputs"]["base_ref"]["description"]
        == "Published release tag to upgrade from. Defaults to the latest published release."
    )
    assert (
        on_section["workflow_dispatch"]["inputs"]["candidate_ref"]["description"]
        == "Candidate ref or SHA to upgrade to. Defaults to the workflow ref/SHA."
    )
    assert workflow["permissions"] == {"contents": "read"}
    assert replay_job["timeout-minutes"] == 75
    assert "services" not in replay_job
    assert replay_job["env"]["OCPP_STATE_REDIS_URL"] == "redis://localhost:6379"
    checkout_step = replay_job["steps"][0]
    assert checkout_step["uses"] == "actions/checkout@v6"
    assert checkout_step["with"]["persist-credentials"] is False
    assert (
        _workflow_step(replay_job, "Start native Redis")["run"].strip()
        == "./scripts/ci/start-native-redis.sh"
    )

    resolve_step = _workflow_step(replay_job, "Resolve replay refs")
    resolve_run = resolve_step["run"]
    assert resolve_step["env"]["BASE_REF_INPUT"] == "${{ inputs.base_ref }}"
    assert resolve_step["env"]["CANDIDATE_REF_INPUT"] == "${{ inputs.candidate_ref }}"
    assert "gh release view --json tagName" in resolve_run
    assert 'gh release view "$base_ref" --json tagName' in resolve_run
    assert '[[ "$base_ref" == refs/tags/* ]]' in resolve_run
    assert (
        'base_sha="$(git rev-parse --verify "refs/tags/${published_base_tag}^{commit}")"'
        in resolve_run
    )
    assert 'base_sha="$(resolve_commit "$base_ref")"' not in resolve_run
    assert 'candidate_ref="${GITHUB_SHA}"' in resolve_run
    assert "resolve_commit()" in resolve_run
    assert (
        "release-upgrade-replay-result-${candidate_sha}-${safe_base_ref}" in resolve_run
    )

    assert (
        _workflow_step(replay_job, "Install suite from release tag")["run"].strip()
        == "./install.sh --no-start"
    )
    release_state_step = _workflow_step(
        replay_job, "Create representative release-era state"
    )
    assert "DOCS_ADMIN_PASSWORD" not in release_state_step.get("env", {})
    release_state_run = release_state_step["run"]
    assert 'DOCS_ADMIN_PASSWORD="$(openssl rand -base64 32)"' in release_state_run
    assert "secrets.DOCS_ADMIN_PASSWORD" not in release_state_run
    assert "python manage.py migrations check" in release_state_run
    assert "python manage.py migrate --noinput --database default" in release_state_run
    assert "python manage.py create_docs_admin --confirm" in release_state_run

    switch_run = _workflow_step(replay_job, "Switch working tree to candidate")["run"]
    assert (
        'git checkout --force "${{ steps.refs.outputs.candidate_sha }}"' in switch_run
    )
    assert "git clean -ffd -e replay-artifacts/" in switch_run

    assert (
        "./upgrade.sh --local --no-restart"
        in _workflow_step(replay_job, "Run real local upgrade path")["run"]
    )
    install_ci_run = _workflow_step(replay_job, "Install CI dependencies")["run"]
    ci_requirements_install = (
        "python -m pip install --only-binary=:all: -r requirements-ci.txt"
    )
    assert install_ci_run.index("./scripts/preflight-env.sh\n") < install_ci_run.index(
        ci_requirements_install
    )
    assert install_ci_run.index(ci_requirements_install) < install_ci_run.index(
        "./scripts/preflight-env.sh --pytest"
    )
    validate_run = _workflow_step(replay_job, "Validate upgraded candidate state")[
        "run"
    ]
    assert "python scripts/check_migration_conflicts.py" in validate_run
    assert "python manage.py migrations check" in validate_run
    assert "python manage.py migrate --noinput --database default" in validate_run
    assert "python scripts/check_import_resolution.py" in validate_run
    benchmark_step = _workflow_step(replay_job, "Capture migration benchmark report")
    benchmark_run = benchmark_step["run"]
    assert benchmark_step["if"] == "always()"
    assert "python manage.py migrations benchmark" in benchmark_run
    assert "--output replay-artifacts/migration-benchmark.json" in benchmark_run
    assert '"status": "unavailable"' in benchmark_run
    assert '"status": "failed"' in benchmark_run
    assert (
        '-m "${UPGRADE_GATE_MARKER}"'
        in _workflow_step(replay_job, "Run release upgrade regression tests")["run"]
    )

    marker_run = _workflow_step(replay_job, "Write replay result marker")["run"]
    assert '"database_backend": "${{ env.ARTHEXIS_DB_BACKEND }}"' in marker_run
    assert "sqlite latest-release-to-candidate replay with native Redis" in marker_run
    assert (
        "PostgreSQL migration coverage remains in install-health shards" in marker_run
    )
    assert "GWAY-like Control validation is evidence-only" in marker_run
    assert "migration_benchmark_artifact" in marker_run
    assert "replay-summary.md" in marker_run

    upload_step = _workflow_step(replay_job, "Upload replay artifacts")
    assert upload_step["if"] == "always()"
    assert upload_step["uses"] == "actions/upload-artifact@v7"
    assert upload_step["with"]["name"] == "${{ steps.refs.outputs.artifact_name }}"


def test_release_upgrade_replay_dispatch_auto_runs_once_per_main_sha() -> None:
    workflow = _workflow_data("release-upgrade-replay-dispatch.yml")
    on_section = _workflow_on(workflow)
    dispatch_job = workflow["jobs"]["dispatch"]
    dispatch_step = _workflow_step(
        dispatch_job, "Dispatch missing release upgrade replay"
    )
    dispatch_script = dispatch_step["with"]["script"]

    assert "workflow_run" not in on_section
    assert on_section["workflow_dispatch"]["inputs"]["base_ref"]["required"] is False
    assert (
        on_section["workflow_dispatch"]["inputs"]["candidate_ref"]["description"]
        == "Candidate ref or SHA to upgrade to. Defaults to the default branch head."
    )
    assert workflow["permissions"] == {"actions": "write", "contents": "read"}
    assert workflow["concurrency"]["group"] == (
        "release-upgrade-replay-dispatch-${{ github.sha }}"
    )

    assert "github.event_name == 'workflow_dispatch'" in dispatch_job["if"]
    assert "github.event.workflow_run" not in dispatch_job["if"]

    assert dispatch_step["env"]["BASE_REF_INPUT"] == (
        "${{ github.event.inputs.base_ref || '' }}"
    )
    assert dispatch_step["env"]["CANDIDATE_REF_INPUT"] == (
        "${{ github.event.inputs.candidate_ref || '' }}"
    )
    assert "const replayWorkflowId = 'release-upgrade-replay.yml'" in dispatch_script
    assert "requiredWorkflowNames = ['Install Health Check']" in dispatch_script
    assert "function matchesCodeqlPath(path)" in dispatch_script
    assert "path === '.github/workflows/codeql.yml'" in dispatch_script
    assert "path.startsWith('.github/codeql/')" in dispatch_script
    assert "async function currentDefaultBranchSha()" in dispatch_script
    assert "github.rest.repos.getBranch" in dispatch_script
    assert "stale_candidate_sha" in dispatch_script
    assert "stale_candidate_sha_before_dispatch" in dispatch_script
    assert "async function collectCurrentHeadFiles(defaultBranchSha)" in dispatch_script
    assert "github.rest.repos.compareCommits" in dispatch_script
    assert "github.rest.actions.listWorkflowRunsForRepo" in dispatch_script
    assert "waiting_for_required_workflow" in dispatch_script
    assert "required_workflow_not_success" in dispatch_script
    assert "unable_to_determine_codeql_relevance" in dispatch_script
    assert "const requiresCodeqlEvidence" in dispatch_script
    assert "No CodeQL-relevant files changed" in dispatch_script
    assert "github.rest.actions.listWorkflowRuns" in dispatch_script
    assert "github.rest.actions.listWorkflowRunArtifacts" in dispatch_script
    assert (
        "release-upgrade-replay-result-${candidateSha}-${safeArtifactRef(baseRef)}"
        in dispatch_script
    )
    assert "release_upgrade_replay_active" in dispatch_script
    assert "release_upgrade_replay_artifact_exists" in dispatch_script
    assert "release_upgrade_replay_already_attempted" not in dispatch_script
    assert (
        "Completed replay runs exist for this candidate, but none produced "
        "the expected current-base artifact." in dispatch_script
    )
    assert "github.rest.actions.createWorkflowDispatch" in dispatch_script
    assert "base_ref: baseRef" in dispatch_script
    assert "candidate_ref: candidateSha" in dispatch_script


def test_release_simulator_requires_successful_release_upgrade_replay() -> None:
    workflow = _workflow_data("release-simulator.yml")
    evaluate_job = workflow["jobs"]["evaluate"]
    evaluate_step = _workflow_step(
        evaluate_job, "Evaluate release blockers from install/upgrade pipeline state"
    )
    evaluate_script = evaluate_step["with"]["script"]
    report_job = workflow["jobs"]["report"]
    report_step = _workflow_step(
        report_job, "Create or update release readiness report issue"
    )
    report_script = report_step["with"]["script"]

    assert evaluate_job["permissions"]["actions"] == "read"
    assert evaluate_job["outputs"]["upgrade_replay_summary"] == (
        "${{ steps.evaluate.outputs.upgrade_replay_summary }}"
    )
    assert "findReleaseUpgradeReplayEvidence" in evaluate_script
    assert "function isReleaseUpgradeReplayRun(run)" in evaluate_script
    assert (
        "const replayWorkflowPath = '.github/workflows/release-upgrade-replay.yml'"
        in evaluate_script
    )
    assert "String(run.path || '').split('@')[0]" in evaluate_script
    assert "runPath === replayWorkflowPath" in evaluate_script
    assert "runPath.endsWith(`/${replayWorkflowPath}`)" in evaluate_script
    assert "runName === 'Release Upgrade Replay'" in evaluate_script
    assert "runName.startsWith('Release Upgrade Replay (')" in evaluate_script
    assert (
        ".filter((run) => isReleaseUpgradeReplayRun(run) && "
        "run.head_sha === defaultBranchSha)" in evaluate_script
    )
    assert "run.head_sha === defaultBranchSha" in evaluate_script
    assert "github.rest.actions.listWorkflowRunArtifacts" in evaluate_script
    assert (
        "release-upgrade-replay-result-${defaultBranchSha}-"
        "${safeArtifactRef(latestReleaseTag)}" in evaluate_script
    )
    assert "Release Upgrade Replay has not run for current" in evaluate_script
    assert "Latest Release Upgrade Replay for current" in evaluate_script
    assert "Release Upgrade Replay has no successful run" in evaluate_script
    assert "core.setOutput('upgrade_replay_summary'" in evaluate_script

    assert report_step["env"]["UPGRADE_REPLAY_SUMMARY"] == (
        "${{ needs.evaluate.outputs.upgrade_replay_summary }}"
    )
    assert "const upgradeReplaySummary" in report_script
    assert "## Release upgrade replay" in report_script
    assert "upgradeReplaySummary" in report_script
    assert (
        "Release readiness requires a successful latest-release-to-candidate replay"
        in report_script
    )


def test_release_simulator_reports_deferred_future_release_issues() -> None:
    workflow = _workflow_data("release-simulator.yml")
    evaluate_job = workflow["jobs"]["evaluate"]
    evaluate_step = _workflow_step(
        evaluate_job, "Evaluate release blockers from install/upgrade pipeline state"
    )
    evaluate_script = evaluate_step["with"]["script"]
    report_job = workflow["jobs"]["report"]
    report_step = _workflow_step(
        report_job, "Create or update release readiness report issue"
    )
    report_script = report_step["with"]["script"]

    assert evaluate_job["outputs"]["future_release_issues_json"] == (
        "${{ steps.evaluate.outputs.future_release_issues_json }}"
    )
    assert evaluate_job["outputs"]["default_branch_version"] == (
        "${{ steps.evaluate.outputs.default_branch_version }}"
    )
    assert "<!-- release-readiness-future-release -->" in evaluate_script
    assert (
        "function futureReleaseDeferral(issue, currentVersion = '')" in evaluate_script
    )
    assert "function normalizeReleaseVersion(version)" in evaluate_script
    assert "function targetReleaseMatchesCurrentVersion" in evaluate_script
    assert "async function readDefaultBranchVersion(ref)" in evaluate_script
    assert "function currentReleaseIssueLabel(issue)" in evaluate_script
    assert "currentReleaseLabel" in evaluate_script
    assert "'bug'" in evaluate_script
    assert "'priority: critical'" in evaluate_script
    assert "'priority: high'" in evaluate_script
    assert "also has current-release label" in evaluate_script
    assert (
        "const currentReleaseLabel = currentReleaseIssueLabel(issue);"
        in evaluate_script
    )
    assert evaluate_script.index(
        "const currentReleaseLabel = currentReleaseIssueLabel(issue);"
    ) < evaluate_script.index(
        "return !labels.some((label) => (label?.name || '').toLowerCase() === blockingIssueLabel);"
    )
    assert "hasTrustedDeferralAuthority" in evaluate_script
    assert "milestoneTitle" in evaluate_script
    assert "future-release" in evaluate_script
    assert "Boolean(milestoneTitle)" not in evaluate_script
    assert "without a maintainer-controlled deferral label" in evaluate_script
    assert "targeted to current VERSION" in evaluate_script
    assert "Target release|Future release" in evaluate_script
    assert "futureReleaseDeferral(issue, defaultBranchVersion)" in evaluate_script
    assert "futureReleaseDeferredIssues.push(futureReleaseIssue)" in evaluate_script
    assert "return false;" in evaluate_script
    assert "core.setOutput('default_branch_version'" in evaluate_script
    assert "core.setOutput('future_release_issues_json'" in evaluate_script
    assert report_step["env"]["DEFAULT_BRANCH_VERSION"] == (
        "${{ needs.evaluate.outputs.default_branch_version }}"
    )
    assert report_step["env"]["FUTURE_RELEASE_ISSUES_JSON"] == (
        "${{ needs.evaluate.outputs.future_release_issues_json }}"
    )
    assert "const futureReleaseIssues = JSON.parse" in report_script
    assert (
        "const defaultBranchVersion = process.env.DEFAULT_BRANCH_VERSION"
        in report_script
    )
    assert "deferredFutureIssueSection" in report_script
    assert "futureReleaseIssues" in report_script
    assert "## Deferred future-release issues" in report_script
    assert "targeted to the current VERSION" in report_script
    assert "targeted to" in report_script


def test_release_simulator_omits_canary_release_readiness_wiring() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    workflow = _workflow_data("release-simulator.yml")
    evaluate_job = workflow["jobs"]["evaluate"]
    evaluate_step = _workflow_step(
        evaluate_job, "Evaluate release blockers from install/upgrade pipeline state"
    )
    evaluate_script = evaluate_step["with"]["script"]

    report_job = workflow["jobs"]["report"]
    report_step = _workflow_step(
        report_job, "Create or update release readiness report issue"
    )
    report_script = report_step["with"]["script"]

    assert "canary_summary" not in evaluate_job["outputs"]
    assert "run.name === 'Deploy Canary'" not in evaluate_script
    assert "canarySummary" not in evaluate_script
    assert "CANARY_SUMMARY" not in report_step["env"]
    assert "Canary deployment" not in report_script
    assert "canarySummary" not in report_script
    assert "Canary evidence is reported when available" not in report_script
    assert not (repo_root / ".github" / "workflows" / "deploy-canary.yml").exists()
    assert not (repo_root / "scripts" / "canary-deploy-validate.sh").exists()


def test_release_simulator_requires_security_scan_settling_and_clear_alerts() -> None:
    workflow = _workflow_data("release-simulator.yml")
    evaluate_job = workflow["jobs"]["evaluate"]
    evaluate_step = _workflow_step(
        evaluate_job, "Evaluate release blockers from install/upgrade pipeline state"
    )
    script = evaluate_step["with"]["script"]

    assert evaluate_job["permissions"]["security-events"] == "read"
    assert "minimumMainCooldownMillis = 10 * 60 * 1000" in script
    assert "defaultBranchHeadObservationRuns" in script
    assert "run.head_sha === defaultBranchSha" in script
    assert "run.id !== context.runId" in script
    assert "run.event === 'push'" not in script
    assert "defaultBranchAdvancedAt" in script
    assert "Minimum release-readiness cooldown has not elapsed since" in script
    assert "before trusting security scan evidence" in script
    assert "Unable to verify when" in script
    assert "github.rest.codeScanning.listAlertsForRepo" in script
    assert "state: 'open'" in script
    assert "Open GitHub code scanning security findings" in script
    assert "Review them in GitHub code scanning" in script
    assert "Unable to verify GitHub code scanning alerts" in script
    assert "legacyCodeScanningTrackerMarker" in script
    assert "<!-- code-scanning-security-findings -->" in script
    assert "github.rest.issues.update" in script
    assert "state: 'closed'" in script
    assert "Closed and redacted legacy code scanning tracker issue" in script
    assert "Unable to redact legacy GitHub code scanning tracker issue" in script
    assert "state: 'all'" in script
    assert "const openIssues = issues.filter" in script
    assert "legacyCodeScanningTrackerNumbers.has(issue.number)" in script
    assert script.index("const legacyCodeScanningTrackerIssues") < script.index(
        "const openBlockingIssues"
    )


def test_release_simulator_does_not_publish_code_scanning_alert_details() -> None:
    workflow = _workflow_data("release-simulator.yml")
    evaluate_job = workflow["jobs"]["evaluate"]
    evaluate_step = _workflow_step(
        evaluate_job, "Evaluate release blockers from install/upgrade pipeline state"
    )
    script = evaluate_step["with"]["script"]

    assert evaluate_job["permissions"]["issues"] == "write"
    assert evaluate_job["permissions"]["security-events"] == "read"
    assert "codeScanningIssueMarker" not in script
    assert "codeScanningIssueLabels" not in script
    assert "codeScanningIssueTitle" not in script
    assert "syncCodeScanningAlertIssue" not in script
    assert "sortCodeScanningAlertSummaries" not in script
    assert "Open alert count: ${alerts.length}" not in script
    assert "<!-- code-scanning-security-fingerprint:" not in script
    assert "Persistent code scanning alert issue" not in script
    assert "rule.security_severity_level" not in script
    assert "rule.severity" not in script
    assert "rule.id" not in script
    assert "rule.name" not in script
    assert "alert.html_url" not in script


def test_release_simulator_pr_and_issue_blockers_have_required_permissions() -> None:
    workflow = _workflow_data("release-simulator.yml")
    evaluate_job = workflow["jobs"]["evaluate"]
    evaluate_step = _workflow_step(
        evaluate_job, "Evaluate release blockers from install/upgrade pipeline state"
    )
    script = evaluate_step["with"]["script"]
    report_job = workflow["jobs"]["report"]
    report_step = _workflow_step(
        report_job, "Create or update release readiness report issue"
    )
    report_script = report_step["with"]["script"]

    assert evaluate_job["permissions"]["issues"] == "write"
    assert evaluate_job["permissions"]["pull-requests"] == "write"
    assert evaluate_job["outputs"]["release_threads_json"] == (
        "${{ steps.evaluate.outputs.release_threads_json }}"
    )
    assert "github.rest.pulls.list" in script
    assert "abandonedReleasePrMarker = '<!-- abandoned-release-pr -->'" in script
    assert "parseAbortedReleasePull" in script
    assert "release/prepare-v${version}" in script
    assert "pull.head?.repo?.full_name" in script
    assert "String(pull.body || '').includes(abandonedReleasePrMarker)" in script
    assert "closedReleasePullRequests" in script
    assert "github.rest.issues.createComment" in script
    assert "github.rest.pulls.update" in script
    assert (
        "explicitly abandoned release candidate should not block or alter the next patch calculation"
        in script
    )
    assert "blockingPullRequests" in script
    assert "core.setOutput('release_threads_json'" in script
    assert "releasePullIsStale" not in script
    assert "updatedAtMillis" not in script

    simulate_job = workflow["jobs"]["simulate_release"]
    resolve_step = _workflow_step(
        simulate_job, "Resolve simulation version against PyPI"
    )
    resolve_run = resolve_step["run"]
    assert "RELEASE_THREADS_JSON" not in resolve_step.get("env", {})
    assert '--github-output "$plan_output"' in resolve_run
    assert "resolve_halted_patch_release_prs" not in resolve_run
    assert "Effective simulated VERSION" not in resolve_run
    assert 'cat "$plan_output" >> "$GITHUB_OUTPUT"' in resolve_run
    assert "releaseReadinessReportTitle = 'Release Readiness Report'" in script
    assert (
        "releaseReadinessReportMarker = '<!-- release-readiness-report -->'" in script
    )
    assert "issue.title === releaseReadinessReportTitle" in script
    assert "issue.body?.includes(releaseReadinessReportMarker)" in script
    assert "RELEASE_THREADS_JSON" not in report_step["env"]
    assert "Closed abandoned release PRs" not in report_script
    assert "explicitly marked abandoned" not in report_script
    assert "closed by this readiness check" not in report_script
    assert "not treated as a readiness blocker" not in report_script
    assert "ignored by the next patch calculation" not in report_script


def test_release_simulator_suppresses_unchanged_report_comments() -> None:
    workflow = _workflow_data("release-simulator.yml")
    report_job = workflow["jobs"]["report"]
    report_step = _workflow_step(
        report_job, "Create or update release readiness report issue"
    )
    script = report_step["with"]["script"]

    assert "const crypto = require('crypto')" in script
    assert "const reportFingerprint = crypto" in script
    assert "<!-- release-readiness-fingerprint:" in script
    assert (
        "const reportUnchanged = existing.body?.includes(fingerprintMarker)" in script
    )
    assert "if (reportUnchanged)" in script
    assert "stableReportPassed" not in script
    assert "already has unchanged content; skipping refresh comment" in script


def test_release_simulator_report_issue_uses_stable_labels() -> None:
    workflow = _workflow_data("release-simulator.yml")
    report_job = workflow["jobs"]["report"]
    report_step = _workflow_step(
        report_job, "Create or update release readiness report issue"
    )
    script = report_step["with"]["script"]

    assert report_job["permissions"]["issues"] == "write"
    assert "const desiredReportLabels = ['automation', 'upgrade']" in script
    assert "github.rest.issues.listLabelsForRepo" in script
    assert "Configured labels not present in repository" in script
    assert (
        "await addAvailableLabels(existing.number, desiredReportLabels, existing)"
        in script
    )
    assert (
        "const createIssueLabels = await availableIssueLabels(desiredReportLabels)"
        in script
    )
    assert (
        "...(createIssueLabels.length ? { labels: createIssueLabels } : {})" in script
    )


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


def test_publish_readiness_gate_requires_tag_at_default_branch() -> None:
    workflow = _workflow_data("publish.yml")
    readiness_job = workflow["jobs"]["readiness-gate"]
    readiness_step = _workflow_step(readiness_job, "Verify release readiness report")
    script = readiness_step["run"]

    assert readiness_step["env"]["DEFAULT_BRANCH"] == (
        "${{ github.event.repository.default_branch }}"
    )
    assert "default_branch_sha" in script
    assert "gh api" in script
    assert "repos/${GITHUB_REPOSITORY}/git/ref/heads/${DEFAULT_BRANCH}" in script
    assert "--jq '.object.sha'" in script
    assert 'if [ "$target_sha" != "$default_branch_sha" ]; then' in script
    assert "Release publish blocked: ${RELEASE_TAG} points to ${target_sha}" in script
    assert 'release_simulator_ref="refs/heads/${DEFAULT_BRANCH}"' in script
    assert 'release_simulator_ref="${RELEASE_REF}"' not in script


def test_publish_workflow_runs_tests_checks_pruning_evidence_and_uploads_log_before_publish_prerequisites() -> (
    None
):
    workflow = _workflow_data("publish.yml")
    test_job = workflow["jobs"]["test"]
    build_job = workflow["jobs"]["build"]

    run_tests_step = _workflow_step(test_job, "Run tests")
    check_pruning_step = _workflow_step(test_job, "Check test pruning evidence")
    upload_log_step = _workflow_step(test_job, "Upload pytest log")
    resolve_step = _workflow_step(build_job, "Resolve publish version against PyPI")

    step_names = [step.get("name") for step in test_job["steps"] if step.get("name")]
    assert step_names.index("Run tests") < step_names.index(
        "Check test pruning evidence"
    )
    assert step_names.index("Check test pruning evidence") < step_names.index(
        "Upload pytest log"
    )
    assert upload_log_step["if"] == "always()"
    assert "--junitxml=pytest-junit.xml" in run_tests_step["run"]
    assert "test pruning evidence" in check_pruning_step["run"]
    assert "prune_low_value_tests" in check_pruning_step["run"]
    assert build_job["needs"] == "test"
    assert "Release publish blocked" in resolve_step["run"]


def test_step_prune_low_value_tests_requires_minor_but_skips_patch(
    monkeypatch, settings, tmp_path: Path
):
    settings.RELEASE_PUBLISH_TEST_PRUNING_PR_URL = ""
    monkeypatch.setattr(pipeline, "_append_log", lambda *_args, **_kwargs: None)
    minor_ctx: dict[str, object] = {"release_previous_version": "1.2.3"}

    with pytest.raises(PublishPending):
        pipeline._step_prune_low_value_tests(
            SimpleNamespace(version="1.3.0"), minor_ctx, tmp_path / "publish.log"
        )

    assert minor_ctx["paused"] is True
    assert minor_ctx["test_pruning_required"] is True
    assert "worst 1% of tests" in minor_ctx["test_pruning_error"]
    assert "error" not in minor_ctx

    patch_ctx: dict[str, object] = {"release_previous_version": "1.2.3"}
    pipeline._step_prune_low_value_tests(
        SimpleNamespace(version="1.2.4"), patch_ctx, tmp_path / "publish.log"
    )

    assert patch_ctx["test_pruning_result"] == {
        "success": True,
        "source": "not_required",
        "reason": "patch_release",
        "previous_version": "1.2.3",
        "version": "1.2.4",
        "criteria": list(pipeline.TEST_PRUNING_CRITERIA),
    }
    assert "test_pruning_required" not in patch_ctx
    assert "test_pruning_error" not in patch_ctx


def test_pre_release_actions_derives_previous_version_after_restart(
    monkeypatch, tmp_path: Path
):
    _init_release_repo(tmp_path, "1.2.3")
    (tmp_path / "VERSION").write_text("1.3.0\n", encoding="utf-8")
    _run_git(tmp_path, "add", "VERSION")
    _run_git(tmp_path, "commit", "-m", "pre-release commit 1.3.0")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, "_sync_with_origin_main", lambda _log_path: None)
    monkeypatch.setattr(pipeline.PackageRelease, "dump_fixture", lambda: None)
    monkeypatch.setattr(pipeline, "_append_log", lambda *_args, **_kwargs: None)
    ctx: dict[str, object] = {}

    pipeline._step_pre_release_actions(
        SimpleNamespace(version="1.3.0"), ctx, tmp_path / "publish.log"
    )

    assert ctx["release_previous_version"] == "1.2.3"
    assert ctx["release_target_version"] == "1.3.0"
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""


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


def test_github_release_body_uses_human_summary(db):
    package = Package.objects.create(name="arthexis")
    release = PackageRelease.objects.create(
        package=package,
        version="1.2.3",
        release_summary="  A focused maintenance release for operators.\n\nIncludes safer release publishing.  ",
    )

    assert pipeline._github_release_body(release) == (
        "A focused maintenance release for operators. Includes safer release publishing."
    )


def test_github_release_body_falls_back_to_general_description(db):
    package = Package.objects.create(name="arthexis")
    release = PackageRelease.objects.create(package=package, version="1.2.3")

    assert pipeline._github_release_body(release) == (
        "arthexis 1.2.3 packages the current suite code, release metadata, "
        "and distribution artifacts into a versioned build that operators can "
        "install or upgrade to through the standard Arthexis release channels."
    )


def test_ensure_github_release_creates_release_with_body():
    calls = []

    class Response:
        status_code = 404
        text = ""

        @staticmethod
        def json():
            return {"id": 10, "body": "Release opening"}

    class CreatedResponse(Response):
        status_code = 201

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "get":
            return Response()
        return CreatedResponse()

    result = pipeline.gh_ensure_github_release(
        request=request,
        owner="acme",
        repo="widget",
        tag_name="v1.2.3",
        token="token",
        body="Release opening",
    )

    assert result["body"] == "Release opening"
    assert calls[1][0] == "post"
    assert calls[1][2]["json"]["body"] == "Release opening"


def test_ensure_github_release_updates_existing_body():
    calls = []

    class ExistingResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"id": 10, "body": "Old opening"}

    class UpdatedResponse(ExistingResponse):
        @staticmethod
        def json():
            return {"id": 10, "body": "New opening"}

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "patch":
            return UpdatedResponse()
        return ExistingResponse()

    result = pipeline.gh_ensure_github_release(
        request=request,
        owner="acme",
        repo="widget",
        tag_name="v1.2.3",
        token="token",
        body="New opening",
    )

    assert result["body"] == "New opening"
    assert calls[1][0] == "patch"
    assert calls[1][2]["json"] == {"body": "New opening"}


@pytest.mark.django_db
def test_release_progress_saves_release_summary(monkeypatch, tmp_path: Path):
    package = Package.objects.create(name="arthexis")
    release = PackageRelease.objects.create(package=package, version="1.2.3")

    class FakeWorkflow:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def load(_log_warning):
            return ReleasePublishContext(step=0, started=False, paused=False), None

        @staticmethod
        def template_state(ctx: ReleasePublishContext):
            return ctx.to_dict()

        @staticmethod
        def start(ctx: ReleasePublishContext, *, start_enabled: bool):
            return ctx

    monkeypatch.setattr(pipeline, "ReleasePublishWorkflow", FakeWorkflow)
    monkeypatch.setattr(
        pipeline, "_get_release_or_response", lambda *_args: (release, None)
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
    monkeypatch.setattr(pipeline.messages, "success", lambda *_args, **_kwargs: None)

    request = RequestFactory().post(
        "/release/publish",
        {
            "set_release_summary": "1",
            "release_summary": "  Human opening.\nMore detail. ",
        },
    )
    request.session = {}
    request.user = type("User", (), {"is_authenticated": False})()
    response = pipeline.release_progress_impl(request, pk=release.pk, action="publish")

    release.refresh_from_db()
    assert response.status_code == 302
    assert release.release_summary == "Human opening. More detail."


@pytest.mark.django_db
def test_release_progress_token_post_does_not_clear_release_summary(
    monkeypatch, tmp_path: Path
):
    package = Package.objects.create(name="arthexis")
    release = PackageRelease.objects.create(
        package=package,
        version="1.2.3",
        release_summary="Existing opening.",
    )

    class FakeWorkflow:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def load(_log_warning):
            return ReleasePublishContext(step=0, started=False, paused=False), None

        @staticmethod
        def template_state(ctx: ReleasePublishContext):
            return ctx.to_dict()

        @staticmethod
        def start(ctx: ReleasePublishContext, *, start_enabled: bool):
            return ctx

        @staticmethod
        def resume(ctx: ReleasePublishContext):
            return ctx, False, HttpResponse("token handled")

    monkeypatch.setattr(pipeline, "ReleasePublishWorkflow", FakeWorkflow)
    monkeypatch.setattr(
        pipeline, "_get_release_or_response", lambda *_args: (release, None)
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

    request = RequestFactory().post(
        "/release/publish",
        {"set_github_token": "1", "github_token": "token"},
    )
    request.session = {}
    request.user = type("User", (), {"is_authenticated": False})()
    response = pipeline.release_progress_impl(request, pk=release.pk, action="publish")

    release.refresh_from_db()
    assert response.content == b"token handled"
    assert release.release_summary == "Existing opening."

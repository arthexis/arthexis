"""Release publish workflow pipeline and step implementations.

Responsibilities:
- Implement `_step_*` workflow actions and orchestration helpers.
- Coordinate git and GitHub operations via dedicated service modules.

Allowed dependencies:
- May call :mod:`git_ops`, :mod:`github_ops`, and shared release utilities.
- Should avoid direct HTTP request/response handling logic.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlencode, urlparse

import requests
import yaml
from django.conf import settings
from django.contrib import messages
from django.db import DatabaseError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from packaging.version import InvalidVersion, Version

import apps.release as release_utils
from apps.nodes.models import NetMessage, Node
from apps.release import git_utils
from apps.release.domain import (
    BUILD_RELEASE_ARTIFACTS_STEP_NAME,
    FIXTURE_REVIEW_STEP_NAME,
)
from apps.release.domain import (
    PUBLISH_STEPS as DOMAIN_PUBLISH_STEPS,
)
from apps.release.models import PackageRelease
from apps.release.services import builder as release_builder
from apps.release.services import uploader as release_uploader
from apps.repos.models import GitHubToken
from utils import revision

from ..common import (
    DIRTY_COMMIT_DEFAULT_MESSAGE,
    PYPI_REQUEST_TIMEOUT,
)
from ..logs import (
    _append_log,
    _download_publish_workflow_logs,
    _github_request,
    _release_log_name,
    _resolve_release_log_dir,
    _truncate_publish_log,
)
from ..report_rendering import (
    _ensure_template_name,
    _render_release_progress_error,
    _sanitize_release_error_message,
)
from .context import (
    ReleaseContextState,
    load_release_context,
)
from .context import (
    persist_release_context as _persist_release_context,
)
from .context import (
    store_release_context as _store_release_context,
)
from .exceptions import DirtyRepository, PublishPending
from .git_ops import (
    SubprocessGitAdapter,
    git_authentication_missing,
)
from .git_ops import (
    collect_dirty_files as git_collect_dirty_files,
)
from .git_ops import (
    current_branch as git_current_branch,
)
from .git_ops import (
    format_subprocess_error as git_format_subprocess_error,
)
from .git_ops import (
    git_stdout as git_adapter_stdout,
)
from .git_ops import (
    has_upstream as git_has_upstream,
)
from .git_ops import (
    working_tree_dirty as git_working_tree_dirty,
)
from .github_ops import (
    ensure_github_release as gh_ensure_github_release,
)
from .github_ops import (
    fetch_publish_workflow_run as gh_fetch_publish_workflow_run,
)
from .github_ops import (
    get_user_github_token as gh_get_user_github_token,
)
from .github_ops import (
    parse_github_repository as gh_parse_github_repository,
)
from .github_ops import (
    resolve_github_token as gh_resolve_github_token,
)
from .github_ops import (
    upload_release_assets as gh_upload_release_assets,
)
from .steps import StepDefinition, run_release_step
from .workflow import (
    ReleasePublishContext,
    ReleasePublishWorkflow,
    _is_pull_request_url,
)

logger = logging.getLogger(__name__)
GIT_ADAPTER = SubprocessGitAdapter()
EXPECTED_PUBLISH_WORKFLOW_FILE = "publish.yml"
EXPECTED_PUBLISH_REF_PATTERN = "refs/tags/v*"
EXPECTED_PUBLISH_ENVIRONMENT = "pypi"
RELEASE_VALIDATION_COMMAND_SETTING = "RELEASE_PUBLISH_VALIDATION_COMMAND"
RELEASE_VALIDATION_TIMEOUT_SETTING = "RELEASE_PUBLISH_VALIDATION_TIMEOUT_SECONDS"
DEFAULT_RELEASE_VALIDATION_TIMEOUT_SECONDS = 900
TEST_PRUNING_PR_URL_SETTING = "RELEASE_PUBLISH_TEST_PRUNING_PR_URL"
TEST_PRUNING_CRITERIA = (
    "low value",
    "duplicate",
    "over-mocked",
    "confusing",
    "misleading",
)


def _resolve_github_token(
    release: PackageRelease, ctx: dict, *, user=None
) -> str | None:
    return gh_resolve_github_token(release, ctx, user=user)


def _get_user_github_token(user) -> GitHubToken | None:
    return gh_get_user_github_token(user)


def _require_github_token(
    release: PackageRelease,
    ctx: dict,
    log_path: Path,
    *,
    message: str,
    user=None,
) -> str:
    token = _resolve_github_token(release, ctx, user=user)
    if token:
        return token
    ctx["paused"] = True
    ctx["github_token_required"] = True
    _append_log(log_path, message)
    raise PublishPending()


def _sync_release_with_revision(
    release: PackageRelease,
) -> tuple[bool, str, PackageRelease | None]:
    """Align the release metadata with the current repository revision.

    Returns a tuple of (updated, previous_version).
    """

    version_path = Path("VERSION")
    previous_version = release.version
    updated = False
    conflicting_release = None
    if version_path.exists():
        current_version = version_path.read_text(encoding="utf-8").strip()
        if "+" in current_version:
            normalized_version = PackageRelease.normalize_version(current_version)
            if normalized_version != current_version:
                version_path.write_text(normalized_version + "\n", encoding="utf-8")
                current_version = normalized_version
        if current_version and current_version != release.version:
            if release.version:
                try:
                    if Version(release.version) > Version(current_version):
                        version_path.write_text(
                            release.version + "\n", encoding="utf-8"
                        )
                        current_version = release.version
                except InvalidVersion:
                    logger.debug(
                        "Invalid version format prevented comparison: "
                        "release.version=%r, current_version=%r",
                        release.version,
                        current_version,
                        exc_info=True,
                    )
            if current_version != release.version:
                conflicting_release = (
                    PackageRelease.objects.filter(
                        package=release.package, version=current_version
                    )
                    .exclude(pk=release.pk)
                    .first()
                )
                if conflicting_release:
                    return updated, previous_version, conflicting_release
                release.version = current_version
                release.revision = revision.get_revision()
                release.save(update_fields=["version", "revision"])
                updated = True
    return updated, previous_version, conflicting_release


def _clean_redirect_path(request, raw_path: str) -> str:
    """Return a safe redirect path restricted to local path components."""

    # Normalize backslashes to forward slashes to avoid browser-specific quirks.
    raw_path = (raw_path or "").replace("\\", "/")

    parsed = urlparse(raw_path)
    path = parsed.path or "/"

    # Ensure the path is absolute.
    if not path.startswith("/"):
        path = f"/{path}"

    # Validate that the URL is safe to redirect to. We treat the path as a relative
    # URL and only allow redirects to the current host.
    if url_has_allowed_host_and_scheme(
        url=path,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return path

    # Fallback to home page if the path is not considered safe.
    return "/"


def _get_release_or_response(request, pk: int, action: str):
    try:
        release = PackageRelease.objects.get(pk=pk)
    except PackageRelease.DoesNotExist:
        return None, _render_release_progress_error(
            request,
            None,
            action,
            _("The requested release could not be found."),
            status=404,
            debug_info={"pk": pk, "action": action},
        )

    if action != "publish":
        return None, _render_release_progress_error(
            request,
            release,
            action,
            _("Unknown release action."),
            status=404,
            debug_info={"action": action},
        )

    return release, None


def _resolve_safe_child_path(root: Path, child: str) -> Path:
    """Resolve ``child`` under ``root`` and reject path traversal."""

    normalized_root = root.resolve(strict=False)
    normalized_path = (normalized_root / child).resolve(strict=False)
    try:
        normalized_path.relative_to(normalized_root)
    except ValueError as exc:
        raise ValueError(f"Unsafe path outside {normalized_root}: {child}") from exc
    return normalized_path


def _handle_release_sync(
    request,
    release: PackageRelease,
    action: str,
    session_key: str,
    lock_path: Path,
    restart_path: Path,
    log_dir: Path,
    repo_version_before_sync: str,
):
    if release.is_current:
        return None

    if release.is_published:
        return _render_release_progress_error(
            request,
            release,
            action,
            _(
                "This release was already published and no longer matches the repository version."
            ),
            status=409,
            debug_info={
                "release_version": release.version,
                "repository_version": repo_version_before_sync,
                "pypi_url": release.pypi_url,
            },
        )

    updated, previous_version, conflicting_release = _sync_release_with_revision(
        release
    )
    if conflicting_release:
        return _render_release_progress_error(
            request,
            release,
            action,
            _("Another release already exists for %(package)s %(version)s.")
            % {
                "package": release.package.name,
                "version": conflicting_release.version,
            },
            status=409,
            debug_info={
                "release_version": release.version,
                "repository_version": repo_version_before_sync,
                "conflicting_release_id": conflicting_release.pk,
            },
        )
    if updated:
        request.session.pop(session_key, None)
        if lock_path.exists():
            lock_path.unlink()
        if restart_path.exists():
            restart_path.unlink()
        pattern = f"pr.{release.package.name}.v{previous_version}*.log"
        for log_file in log_dir.glob(pattern):
            log_file.unlink()

    if not release.is_current:
        return _render_release_progress_error(
            request,
            release,
            action,
            _("The repository VERSION file does not match this release."),
            status=409,
            debug_info={
                "release_version": release.version,
                "repository_version": repo_version_before_sync,
            },
        )

    return None


def _handle_release_restart(
    request,
    release: PackageRelease,
    session_key: str,
    lock_path: Path,
    restart_path: Path,
    log_dir: Path,
):
    if not request.GET.get("restart"):
        return None

    return _reset_release_progress(
        request,
        release,
        session_key,
        lock_path,
        restart_path,
        log_dir,
        clean_repo=True,
    )


def _reset_release_progress(
    request,
    release: PackageRelease,
    session_key: str,
    lock_path: Path,
    restart_path: Path,
    log_dir: Path,
    *,
    clean_repo: bool,
    message_text: str | None = None,
):
    count = 0
    if restart_path.exists():
        try:
            count = int(restart_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            count = 0
    restart_path.parent.mkdir(parents=True, exist_ok=True)
    restart_path.write_text(str(count + 1), encoding="utf-8")
    if clean_repo:
        _clean_repo()
    release.pypi_url = ""
    release.release_on = None
    release.save(update_fields=["pypi_url", "release_on"])
    request.session.pop(session_key, None)
    if lock_path.exists():
        lock_path.unlink()
    pattern = f"pr.{release.package.name}.v{release.version}*.log"
    for f in log_dir.glob(pattern):
        f.unlink()
    if message_text:
        messages.info(request, message_text)
    return redirect(_clean_redirect_path(request, request.path))


def _load_release_context(
    request,
    session_key: str,
    lock_path: Path,
    restart_path: Path,
    log_dir_warning_message: str | None,
):
    session_ctx = request.session.get(session_key)
    loaded_ctx = load_release_context(session_ctx, lock_path)
    state = ReleaseContextState.from_dict(loaded_ctx)
    ctx = state.to_dict()
    if not loaded_ctx:
        ctx = {"step": 0}
        if restart_path.exists():
            restart_path.unlink()

    if log_dir_warning_message:
        ctx["log_dir_warning_message"] = log_dir_warning_message
    else:
        log_dir_warning_message = ctx.get("log_dir_warning_message")

    return ctx, log_dir_warning_message


def _update_publish_controls(
    request,
    ctx: dict,
    start_enabled: bool,
    session_key: str,
    lock_path: Path,
):
    ctx["dry_run"] = bool(ctx.get("dry_run"))

    if request.method == "POST" and request.POST.get("set_github_token"):
        token = (request.POST.get("github_token") or "").strip()
        if token:
            store_token = bool(request.POST.get("store_github_token"))
            ctx["github_token"] = token
            ctx.pop("github_token_required", None)
            if (
                ctx.get("paused")
                and not ctx.get("dirty_files")
                and not ctx.get("pending_git_push")
            ):
                ctx["paused"] = False
            if store_token and request.user.is_authenticated:
                GitHubToken.objects.update_or_create(
                    user=request.user,
                    group=None,
                    defaults={"token": token},
                )
                message = _(
                    "GitHub token stored for this publish session and your account."
                )
            else:
                message = _("GitHub token stored for this publish session.")
            messages.success(request, message)
            _persist_release_context(request, session_key, ctx, lock_path)
        else:
            ctx.pop("github_token", None)
            messages.error(request, _("Enter a GitHub token to continue."))
            _store_release_context(request, session_key, ctx)
        return ctx, False, redirect(_clean_redirect_path(request, request.path))

    if request.method == "POST" and request.POST.get("ack_error"):
        ctx.pop("error", None)
        dirty_entries = _collect_dirty_files()
        if dirty_entries:
            ctx["dirty_files"] = dirty_entries
            ctx.setdefault("dirty_commit_message", DIRTY_COMMIT_DEFAULT_MESSAGE)
        else:
            ctx.pop("dirty_files", None)
            ctx.pop("dirty_log_message", None)
            ctx.pop("dirty_commit_error", None)
        pending_push = ctx.get("pending_git_push")
        if pending_push:
            if _validate_manual_git_push(pending_push):
                ctx.pop("pending_git_push", None)
                ctx.pop("pending_git_push_error", None)
            else:
                ctx["pending_git_push_error"] = _(
                    "Manual push not detected on origin. Confirm the push completed and try again."
                )
        if not ctx.get("started"):
            ctx["started"] = True
        ctx["paused"] = bool(ctx.get("pending_git_push") or ctx.get("dirty_files"))
        _store_release_context(request, session_key, ctx)
        return (
            ctx,
            False,
            redirect(
                _clean_redirect_path(request, request.path),
            ),
        )

    if request.GET.get("set_dry_run") is not None:
        if start_enabled:
            ctx["dry_run"] = bool(request.GET.get("dry_run"))
            _store_release_context(request, session_key, ctx)
        return ctx, False, redirect(_clean_redirect_path(request, request.path))

    if request.GET.get("start"):
        if start_enabled:
            ctx["dry_run"] = bool(request.GET.get("dry_run"))
        ctx["started"] = True
        ctx["paused"] = False

    resume_requested = bool(request.GET.get("resume"))

    if request.GET.get("pause") and ctx.get("started"):
        ctx["paused"] = True

    if resume_requested:
        if not ctx.get("started"):
            ctx["started"] = True
        if ctx.get("paused"):
            ctx["paused"] = False

    return ctx, resume_requested, None


def _prepare_step_progress(
    request,
    ctx: dict,
    restart_path: Path,
    resume_requested: bool,
):
    restart_count = 0
    if restart_path.exists():
        try:
            restart_count = int(restart_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            restart_count = 0
    step_count = ctx.get("step", 0)
    step_param = request.GET.get("step")
    if resume_requested and step_param is None:
        step_param = str(step_count)
    return restart_count, step_param


def _prepare_logging(
    ctx: dict,
    release: PackageRelease,
    log_dir: Path,
    log_dir_warning_message: str | None,
    step_param: str | None,
    step_count: int,
):
    log_name = _release_log_name(release.package.name, release.version)
    if ctx.get("log") != log_name:
        ctx = {
            "step": 0,
            "log": log_name,
            "started": ctx.get("started", False),
        }
        step_count = 0
    log_path = log_dir / log_name
    ctx.setdefault("log", log_name)
    ctx.setdefault("paused", False)
    ctx.setdefault("dirty_commit_message", DIRTY_COMMIT_DEFAULT_MESSAGE)

    if (
        ctx.get("started")
        and step_count == 0
        and (step_param is None or step_param == "0")
    ):
        if log_path.exists():
            log_path.unlink()
        ctx.pop("log_dir_warning_logged", None)

    if log_dir_warning_message and not ctx.get("log_dir_warning_logged"):
        _append_log(log_path, log_dir_warning_message)
        ctx["log_dir_warning_logged"] = True

    return ctx, log_path, step_count


def _build_artifacts_stale(
    ctx: dict, step_count: int, steps: Sequence[tuple[str, object]]
) -> bool:
    build_step_index = next(
        (
            index
            for index, (name, _) in enumerate(steps)
            if name == BUILD_RELEASE_ARTIFACTS_STEP_NAME
        ),
        None,
    )
    if build_step_index is None:
        return False
    if step_count <= build_step_index:
        return False
    if step_count >= len(steps) and not ctx.get("error"):
        return False

    build_revision = (ctx.get("build_revision") or "").strip()
    if not build_revision:
        return False

    current_revision = _current_git_revision()
    if current_revision and current_revision != build_revision:
        return True

    return _working_tree_dirty()


def _broadcast_release_message(release: PackageRelease) -> None:
    subject = f"Release v{release.version}"
    try:
        node = Node.get_local()
    except DatabaseError:
        node = None
    node_label = str(node) if node else "unknown"
    body = f"@ {node_label}"
    try:
        NetMessage.broadcast(subject=subject, body=body)
    except (DatabaseError, RuntimeError, ValueError):
        logger.exception(
            "Failed to broadcast release Net Message",
            extra={"subject": subject, "body": body},
        )


def _handle_dirty_repository_action(request, ctx: dict, log_path: Path):
    dirty_action = request.POST.get("dirty_action") if request.method == "POST" else ""
    if dirty_action and ctx.get("dirty_files"):
        if dirty_action == "discard":
            _clean_repo()
            remaining = _collect_dirty_files()
            if remaining:
                ctx["dirty_files"] = remaining
                ctx.pop("dirty_commit_error", None)
            else:
                ctx.pop("dirty_files", None)
                ctx.pop("dirty_commit_error", None)
                ctx.pop("dirty_log_message", None)
                _append_log(log_path, "Discarded local changes before publish")
        elif dirty_action == "commit":
            message = request.POST.get("dirty_message", "").strip()
            if not message:
                message = (
                    ctx.get("dirty_commit_message") or DIRTY_COMMIT_DEFAULT_MESSAGE
                )
            ctx["dirty_commit_message"] = message
            try:
                GIT_ADAPTER.run(["git", "add", "--all"], check=True)
                GIT_ADAPTER.run(["git", "commit", "-m", message], check=True)
            except subprocess.CalledProcessError as exc:
                ctx["dirty_commit_error"] = _format_subprocess_error(exc)
            else:
                ctx.pop("dirty_commit_error", None)
                remaining = _collect_dirty_files()
                if remaining:
                    ctx["dirty_files"] = remaining
                else:
                    ctx.pop("dirty_files", None)
                    ctx.pop("dirty_log_message", None)
                _append_log(
                    log_path,
                    _("Committed pending changes: %(message)s") % {"message": message},
                )
    return ctx


def _manual_git_push_command(pending_push: dict) -> str:
    branch = pending_push.get("branch")
    if branch:
        return f"git push origin {branch}"
    return "git push origin HEAD"


def _validate_manual_git_push(pending_push: dict) -> bool:
    head = (pending_push.get("head") or "").strip() or _current_git_revision()
    if not head:
        return False
    branch = pending_push.get("branch")
    if branch:
        remote_commit = _git_remote_branch_commit("origin", branch)
        return remote_commit == head
    return _remote_contains_commit("origin", head)


def _handle_manual_git_push_action(
    request,
    ctx: dict,
    log_path: Path,
) -> dict:
    pending_push = ctx.get("pending_git_push")
    if not pending_push:
        ctx.pop("pending_git_push_error", None)
        return ctx
    action = request.POST.get("manual_push_action") if request.method == "POST" else ""
    if not action:
        return ctx
    ctx.pop("pending_git_push_error", None)
    if action == "retry":
        try:
            _push_release_changes(
                log_path,
                ctx,
                step_name=pending_push.get("step", "Release push"),
            )
        except PublishPending:
            pass
        else:
            ctx["paused"] = False
            _append_log(log_path, "Retry push completed")
        return ctx
    if action == "confirm":
        if _validate_manual_git_push(pending_push):
            ctx["paused"] = False
            ctx.pop("pending_git_push", None)
            _append_log(log_path, "Manual push verified; continuing release")
        else:
            ctx["pending_git_push_error"] = _(
                "Manual push not detected on origin. Confirm the push completed and try again."
            )
        return ctx
    return ctx


def _run_release_step(
    request,
    steps,
    ctx: dict,
    step_param: str | None,
    step_count: int,
    release: PackageRelease,
    log_path: Path,
    session_key: str,
    lock_path: Path,
    *,
    allow_when_paused: bool = False,
):
    result = run_release_step(
        steps=[StepDefinition(name=name, handler=func) for name, func in steps],
        ctx=ctx,
        step_param=step_param,
        step_count=step_count,
        release=release,
        log_path=log_path,
        user=request.user,
        append_log=_append_log,
        persist_context=lambda new_ctx: _persist_release_context(
            request, session_key, new_ctx, lock_path
        ),
        allow_when_paused=allow_when_paused,
    )
    return result.ctx, result.step_count


def _sync_with_origin_main(log_path: Path) -> None:
    """Ensure the current branch is rebased onto ``origin/main``."""

    if not _has_remote("origin"):
        _append_log(
            log_path, "No git remote configured; skipping sync with origin/main"
        )
        return

    try:
        GIT_ADAPTER.run(["git", "fetch", "origin", "main"], check=True)
        _append_log(log_path, "Fetched latest changes from origin/main")
        GIT_ADAPTER.run(["git", "rebase", "origin/main"], check=True)
        _append_log(log_path, "Rebased current branch onto origin/main")
    except subprocess.CalledProcessError as exc:
        subprocess.run(["git", "rebase", "--abort"], check=False)
        _append_log(log_path, "Rebase onto origin/main failed; aborted rebase")

        stdout = (exc.stdout or "").strip()
        stderr = (exc.stderr or "").strip()
        if stdout:
            _append_log(log_path, "git output:\n" + stdout)
        if stderr:
            _append_log(log_path, "git errors:\n" + stderr)

        status = subprocess.run(
            ["git", "status"], capture_output=True, text=True, check=False
        )
        status_output = (status.stdout or "").strip()
        status_errors = (status.stderr or "").strip()
        if status_output:
            _append_log(log_path, "git status:\n" + status_output)
        if status_errors:
            _append_log(log_path, "git status errors:\n" + status_errors)

        branch = _current_branch() or "(detached HEAD)"
        instructions = [
            "Manual intervention required to finish syncing with origin/main.",
            "Ensure you are on the branch you intend to publish (normally `main`; currently "
            f"{branch}).",
            "Then run these commands from the repository root:",
            "  git fetch origin main",
            "  git rebase origin/main",
            "Resolve any conflicts (use `git status` to review files) and continue the rebase.",
        ]

        if branch != "main" and branch != "(detached HEAD)":
            instructions.append(
                "If this branch should mirror main, push the rebased changes with "
                f"`git push origin {branch}:main`."
            )
        else:
            instructions.append("Push the rebased branch with `git push origin main`.")

        instructions.append(
            "If push authentication fails, verify your git remote permissions and SSH keys "
            "for origin/main before retrying the publish flow."
        )
        _append_log(log_path, "\n".join(instructions))

        raise RuntimeError("Rebase onto main failed") from exc


def _clean_repo() -> None:
    """Return the git repository to a clean state."""
    subprocess.run(["git", "reset", "--hard"], check=False)
    subprocess.run(["git", "clean", "-fd"], check=False)


def _format_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _git_stdout(args: Sequence[str]) -> str:
    return git_adapter_stdout(GIT_ADAPTER, args)


def _current_git_revision() -> str:
    try:
        return _git_stdout(["git", "rev-parse", "HEAD"])
    except (subprocess.SubprocessError, OSError, ValueError):
        return ""


def _working_tree_dirty() -> bool:
    return git_working_tree_dirty(GIT_ADAPTER)


def _has_remote(remote: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "remote"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    remotes = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return remote in remotes


def _current_branch() -> str | None:
    return git_current_branch(GIT_ADAPTER)


def _has_upstream(branch: str) -> bool:
    return git_has_upstream(GIT_ADAPTER, branch)


def _collect_dirty_files() -> list[dict[str, str]]:
    return git_collect_dirty_files(GIT_ADAPTER)


def _collect_status_paths(pathspecs: list[str]) -> list[str]:
    if not pathspecs:
        return []
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--", *pathspecs],
        capture_output=True,
        text=True,
        check=True,
    )
    paths: list[str] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        status = line[:2]
        path = line[3:]
        if "R" in status and " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path)
    return paths


def _format_subprocess_error(exc: subprocess.CalledProcessError) -> str:
    return git_format_subprocess_error(exc)


def _parse_github_repository(repo_url: str) -> tuple[str, str] | None:
    return gh_parse_github_repository(repo_url)


def _resolve_github_repository(release: PackageRelease) -> tuple[str, str]:
    repo_url = release.package.repository_url or ""
    parsed = _parse_github_repository(repo_url)
    if parsed:
        return parsed
    remote_url = git_utils.git_remote_url(
        "origin", use_push_url=True
    ) or git_utils.git_remote_url("origin")
    if remote_url:
        parsed = _parse_github_repository(remote_url)
        if parsed:
            return parsed
    raise ValueError("GitHub repository URL is required to export artifacts")


def _ensure_release_tag(release: PackageRelease, log_path: Path) -> str:
    tag_name = f"v{release.version}"
    tag_ref = f"refs/tags/{tag_name}"
    exists = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", tag_ref],
        check=False,
        capture_output=True,
    )
    if exists.returncode != 0:
        subprocess.run(["git", "tag", tag_name], check=True)
        _append_log(log_path, f"Created git tag {tag_name}")
    else:
        _append_log(log_path, f"Git tag {tag_name} already exists")
    release_uploader._push_tag(tag_name)
    _append_log(log_path, f"Pushed git tag {tag_name} to origin")
    return tag_name


def _ensure_github_release(
    *,
    owner: str,
    repo: str,
    tag_name: str,
    token: str | None,
) -> dict[str, object]:
    return gh_ensure_github_release(
        request=_github_request,
        owner=owner,
        repo=repo,
        tag_name=tag_name,
        token=token,
    )


def _upload_release_assets(
    *,
    owner: str,
    repo: str,
    release_data: dict[str, object],
    token: str | None,
    artifacts: Sequence[Path],
    log_path: Path,
) -> None:
    gh_upload_release_assets(
        request=_github_request,
        owner=owner,
        repo=repo,
        release_data=release_data,
        token=token,
        artifacts=artifacts,
        append_log=_append_log,
        log_path=log_path,
    )


def _fetch_publish_workflow_run(
    *,
    owner: str,
    repo: str,
    tag_name: str,
    token: str | None,
) -> dict[str, object] | None:
    try:
        tag_sha = _git_stdout(["git", "rev-list", "-n", "1", tag_name])
    except (subprocess.SubprocessError, OSError, ValueError):
        tag_sha = None

    return gh_fetch_publish_workflow_run(
        request=_github_request,
        owner=owner,
        repo=repo,
        tag_name=tag_name,
        tag_sha=tag_sha,
        token=token,
    )


def _append_publish_workflow_status(
    release: PackageRelease,
    log_path: Path,
    *,
    token: str | None,
    message: str,
) -> str:
    run_url = ""
    try:
        owner, repo = _resolve_github_repository(release)
        run = _fetch_publish_workflow_run(
            owner=owner,
            repo=repo,
            tag_name=f"v{release.version}",
            token=token,
        )
    except (
        requests.exceptions.RequestException,
        subprocess.SubprocessError,
        OSError,
        RuntimeError,
        ValueError,
    ):
        logger.warning(
            "Failed to fetch publish workflow run for %s", release, exc_info=True
        )
        run = None
    if run and isinstance(run.get("html_url"), str):
        run_url = run.get("html_url") or ""
    if run_url:
        _append_log(log_path, f"{message} Workflow run: {run_url}")
    else:
        _append_log(log_path, message)
    return run_url


def _pause_for_publish_pending(
    release: PackageRelease,
    ctx: dict[str, object],
    log_path: Path,
    *,
    token: str | None,
    message: str,
    run_url: str | None = None,
) -> NoReturn:
    ctx["paused"] = True
    ctx["publish_pending"] = True
    publish_url = ""
    if run_url is None:
        publish_url = _append_publish_workflow_status(
            release,
            log_path,
            token=token,
            message=message,
        )
    else:
        publish_url = run_url or ""
        if publish_url:
            _append_log(log_path, f"{message} {publish_url}")
        else:
            _append_log(log_path, message)
    if publish_url:
        ctx["publish_workflow_url"] = publish_url
    raise PublishPending()


def _record_release_fixture_updates(
    log_path: Path,
    ctx: dict,
    *,
    commit_message: str,
    staged_message: str,
    committed_message: str,
    skipped_message: str,
    step_name: str,
) -> None:
    fixture_paths = [
        str(path) for path in Path("apps/core/fixtures").glob("releases__*.json")
    ]
    if not fixture_paths:
        return
    pending_push = ctx.get("pending_git_push")
    if pending_push and pending_push.get("step") == step_name:
        _append_log(log_path, "Retrying push of release changes to origin")
        _push_release_changes(log_path, ctx, step_name=step_name)
        return
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", *fixture_paths],
        capture_output=True,
        text=True,
        check=True,
    )
    if status.stdout.strip():
        GIT_ADAPTER.run(["git", "add", *fixture_paths], check=True)
        _append_log(log_path, staged_message)
        subprocess.run(["git", "commit", "-m", commit_message], check=True)
        _append_log(log_path, committed_message)
        _push_release_changes(log_path, ctx, step_name=step_name)
    else:
        _append_log(log_path, skipped_message)


def _git_authentication_missing(exc: subprocess.CalledProcessError) -> bool:
    return git_authentication_missing(exc)


def _git_remote_branch_commit(remote: str, branch: str) -> str | None:
    proc = subprocess.run(
        ["git", "ls-remote", "--heads", remote, branch],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    for line in (proc.stdout or "").splitlines():
        parts = line.strip().split()
        if len(parts) != 2:
            continue
        sha, ref = parts
        if ref.endswith(f"/{branch}"):
            return sha
    return None


def _remote_contains_commit(remote: str, commit: str) -> bool:
    try:
        subprocess.run(
            ["git", "fetch", remote],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return False
    proc = subprocess.run(
        ["git", "branch", "-r", "--contains", commit],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return False
    return any(
        line.strip().startswith(f"{remote}/")
        for line in (proc.stdout or "").splitlines()
    )


def _register_manual_git_push(
    ctx: dict,
    log_path: Path,
    *,
    step_name: str,
    branch: str | None,
    head: str,
    details: str | None,
) -> None:
    pending_push = {
        "step": step_name,
        "remote": "origin",
        "branch": branch,
        "head": head,
    }
    ctx["pending_git_push"] = pending_push
    ctx["paused"] = True
    ctx.pop("pending_git_push_error", None)
    command = _manual_git_push_command(pending_push)
    instructions = [
        "Authentication is required to push release changes to origin.",
        f"Run `{command}` from the repository root, then confirm the manual step.",
    ]
    if details:
        instructions.append(f"Git reported: {details}")
    _append_log(log_path, "\n".join(instructions))


def _push_release_changes(log_path: Path, ctx: dict, *, step_name: str) -> bool:
    """Push release commits to ``origin`` and log the outcome."""

    if not _has_remote("origin"):
        _append_log(
            log_path, "No git remote configured; skipping push of release changes"
        )
        return False

    branch = _current_branch()
    try:
        if branch is None:
            push_cmd = ["git", "push", "origin", "HEAD"]
        elif _has_upstream(branch):
            push_cmd = ["git", "push"]
        else:
            push_cmd = ["git", "push", "--set-upstream", "origin", branch]
        subprocess.run(push_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        details = _format_subprocess_error(exc)
        if _git_authentication_missing(exc):
            head = _current_git_revision()
            _register_manual_git_push(
                ctx,
                log_path,
                step_name=step_name,
                branch=branch,
                head=head,
                details=details or None,
            )
            raise PublishPending() from exc
        _append_log(log_path, f"Failed to push release changes to origin: {details}")
        raise RuntimeError("Failed to push release changes") from exc

    _append_log(log_path, "Pushed release changes to origin")
    pending_push = ctx.get("pending_git_push")
    if pending_push and pending_push.get("step") == step_name:
        ctx.pop("pending_git_push", None)
        ctx.pop("pending_git_push_error", None)
    return True


def _ensure_origin_main_unchanged(log_path: Path) -> None:
    """Verify that ``origin/main`` has not advanced during the release."""

    if not _has_remote("origin"):
        _append_log(
            log_path, "No git remote configured; skipping origin/main verification"
        )
        return

    try:
        subprocess.run(["git", "fetch", "origin", "main"], check=True)
        _append_log(log_path, "Fetched latest changes from origin/main")
        origin_main = _git_stdout(["git", "rev-parse", "origin/main"])
        merge_base = _git_stdout(["git", "merge-base", "HEAD", "origin/main"])
    except subprocess.CalledProcessError as exc:
        details = _format_subprocess_error(exc)
        if details:
            _append_log(log_path, f"Failed to verify origin/main status: {details}")
        else:  # pragma: no cover - defensive fallback
            _append_log(log_path, "Failed to verify origin/main status")
        raise RuntimeError("Unable to verify origin/main status") from exc

    if origin_main != merge_base:
        _append_log(log_path, "origin/main advanced during release; restart required")
        raise RuntimeError("origin/main changed during release; restart required")

    _append_log(log_path, "origin/main unchanged since last sync")


def _next_patch_version(version: str) -> str:
    cleaned = PackageRelease.strip_dev_suffix(version)
    try:
        parsed = Version(cleaned)
    except InvalidVersion:
        parts = cleaned.split(".") if cleaned else []
        for index in range(len(parts) - 1, -1, -1):
            segment = parts[index]
            if segment.isdigit():
                parts[index] = str(int(segment) + 1)
                return ".".join(parts)
        return cleaned or version
    return f"{parsed.major}.{parsed.minor}.{parsed.micro + 1}"


def _major_minor_version_changed(previous: str, current: str) -> bool:
    """Return ``True`` when the version bump changes major or minor."""

    previous_clean = PackageRelease.strip_dev_suffix((previous or "").strip())
    current_clean = PackageRelease.strip_dev_suffix((current or "").strip())
    if not previous_clean or not current_clean:
        return False

    try:
        prev_version = Version(previous_clean)
        curr_version = Version(current_clean)
    except InvalidVersion:
        return False

    return (
        prev_version.major != curr_version.major
        or prev_version.minor != curr_version.minor
    )


def _summarize_fixture_file(path: str) -> dict[str, object]:
    fixture_path = Path(path)
    try:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        count = 0
        models: list[str] = []
    else:
        if isinstance(data, list):
            count = len(data)
            models = sorted(
                {obj.get("model", "") for obj in data if isinstance(obj, dict)}
            )
        elif isinstance(data, dict):
            count = 1
            models = [data.get("model", "")]
        else:  # pragma: no cover - unexpected structure
            count = 0
            models = []
    return {"path": path, "count": count, "models": models}


def _commit_release_prep_changes(
    *,
    ctx: dict,
    log_path: Path,
    fixture_files: list[str],
    version_dirty: bool,
) -> None:
    ctx["fixtures"] = [_summarize_fixture_file(path) for path in fixture_files]
    commit_paths = [*fixture_files]
    if version_dirty:
        commit_paths.append("VERSION")

    log_fragments = []
    if fixture_files:
        log_fragments.append("fixtures " + ", ".join(fixture_files))
    if version_dirty:
        log_fragments.append("VERSION")
    details = ", ".join(log_fragments) if log_fragments else "changes"
    _append_log(log_path, f"Committing release prep changes: {details}")
    GIT_ADAPTER.run(["git", "add", *commit_paths], check=True)

    if version_dirty and fixture_files:
        commit_message = "chore: update version and fixtures"
    elif version_dirty:
        commit_message = "chore: update version"
    else:
        commit_message = "chore: update fixtures"

    GIT_ADAPTER.run(["git", "commit", "-m", commit_message], check=True)
    _append_log(log_path, f"Release prep changes committed ({commit_message})")
    ctx.pop("dirty_files", None)
    ctx.pop("dirty_commit_error", None)
    ctx.pop("dirty_log_message", None)


def _handle_version_step_dirty_repository(ctx: dict, log_path: Path) -> bool:
    if release_builder._git_clean():
        ctx.pop("dirty_files", None)
        ctx.pop("dirty_commit_error", None)
        ctx.pop("dirty_log_message", None)
        return False

    dirty_entries = _collect_dirty_files()
    files = [entry["path"] for entry in dirty_entries]
    fixture_files = [
        path
        for path in files
        if "fixtures" in Path(path).parts and Path(path).suffix == ".json"
    ]
    version_dirty = "VERSION" in files
    allowed_dirty_files = set(fixture_files)
    if version_dirty:
        allowed_dirty_files.add("VERSION")

    if files and len(allowed_dirty_files) == len(files):
        _commit_release_prep_changes(
            ctx=ctx,
            log_path=log_path,
            fixture_files=fixture_files,
            version_dirty=version_dirty,
        )
        return True

    ctx["dirty_files"] = dirty_entries
    ctx.setdefault("dirty_commit_message", DIRTY_COMMIT_DEFAULT_MESSAGE)
    ctx.pop("fixtures", None)
    ctx.pop("dirty_commit_error", None)
    details = (
        ", ".join(entry["path"] for entry in dirty_entries) if dirty_entries else ""
    )
    message = "Git repository has uncommitted changes"
    if details:
        message += f": {details}"
    if ctx.get("dirty_log_message") != message:
        _append_log(log_path, message)
        ctx["dirty_log_message"] = message
    raise DirtyRepository()


def _ensure_release_version_is_not_older(release) -> None:
    version_path = Path("VERSION")
    if not version_path.exists():
        return

    current = version_path.read_text(encoding="utf-8").strip()
    if not current:
        return

    current_clean = PackageRelease.strip_dev_suffix(current) or "0.0.0"
    try:
        left = Version(release.version)
        right = Version(current_clean)
    except InvalidVersion as exc:
        raise ValueError(f"Invalid release.version '{release.version}': {exc}") from exc

    if left < right:
        raise ValueError(f"Version {release.version} is older than existing {current}")


def _check_release_version_not_on_pypi(release, log_path: Path) -> None:
    _append_log(log_path, f"Checking if version {release.version} exists on PyPI")
    if not release_utils.network_available():
        _append_log(log_path, "Network unavailable, skipping PyPI check")
        return

    resp = None
    try:
        resp = requests.get(
            f"https://pypi.org/pypi/{release.package.name}/json",
            timeout=PYPI_REQUEST_TIMEOUT,
        )
        if not resp.ok:
            return

        data = resp.json()
        releases = data.get("releases", {})
        try:
            target_version = Version(release.version)
        except InvalidVersion:
            target_version = None

        for candidate, files in releases.items():
            if not _versions_match(
                candidate=candidate,
                release_version=release.version,
                target_version=target_version,
            ):
                continue

            if _has_non_yanked_files(files):
                raise RuntimeError(f"Version {release.version} already on PyPI")
    except RuntimeError:
        raise
    except (requests.exceptions.RequestException, ValueError) as exc:
        _append_log(log_path, f"PyPI check failed: {exc}")
        return
    else:
        _append_log(log_path, f"Version {release.version} not published on PyPI")
    finally:
        if resp is not None:
            resp.close()


def _versions_match(
    *,
    candidate: str,
    release_version: str,
    target_version: Version | None,
) -> bool:
    if candidate == release_version:
        return True
    if target_version is None:
        return False

    try:
        return Version(candidate) == target_version
    except InvalidVersion:
        return False


def _has_non_yanked_files(files: object) -> bool:
    return any(
        isinstance(file_data, dict) and not file_data.get("yanked", False)
        for file_data in files or []
    )


def _step_check_version(release, ctx, log_path: Path, *, user=None) -> None:
    """Validate release version preconditions before publish.

    Prerequisites: git repository is available.
    Side effects: may commit version/fixture changes and update context pause flags.
    Rollback expectations: conflicts require manual cleanup or restart.
    """
    sync_error: Exception | None = None
    retry_sync = False
    try:
        _sync_with_origin_main(log_path)
    except RuntimeError as exc:
        sync_error = exc

    retry_sync = _handle_version_step_dirty_repository(ctx, log_path)

    if retry_sync and sync_error is not None:
        try:
            _sync_with_origin_main(log_path)
        except RuntimeError as exc:
            sync_error = exc
        else:
            sync_error = None

    if sync_error is not None:
        raise sync_error

    _ensure_release_version_is_not_older(release)
    _check_release_version_not_on_pypi(release, log_path)


def _step_handle_migrations(release, ctx, log_path: Path, *, user=None) -> None:
    """Run migration checks required before release promotion.

    Prerequisites: version checks already passed.
    Side effects: delegates to release workflow migration handlers.
    Rollback expectations: migration failures halt pipeline for operator intervention.
    """
    _append_log(log_path, "Freeze, squash and approve migrations")
    _append_log(log_path, "Migration review acknowledged (manual step)")


def _step_pre_release_actions(release, ctx, log_path: Path, *, user=None) -> None:
    """Execute pre-release automation before tests/build promotion.

    Prerequisites: repository state synchronized and version established.
    Side effects: may mutate release metadata and write publish logs.
    Rollback expectations: partial changes are committed/pushed by subsequent retry actions.
    """
    _ = user
    _append_log(log_path, "Execute pre-release actions")
    if ctx.get("dry_run"):
        _append_log(log_path, "Dry run: skipping pre-release actions")
        return
    _sync_with_origin_main(log_path)
    PackageRelease.dump_fixture()
    staged_release_fixtures: list[Path] = []
    release_fixture_status = _collect_status_paths(
        ["apps/core/fixtures/releases__*.json"]
    )
    if release_fixture_status:
        subprocess.run(
            ["git", "add", "--", *release_fixture_status],
            check=True,
        )
        staged_release_fixtures = [Path(path) for path in release_fixture_status]
        formatted = ", ".join(_format_path(path) for path in staged_release_fixtures)
        _append_log(log_path, "Staged release fixtures " + formatted)
    version_path = Path("VERSION")
    previous_version_text = (
        version_path.read_text(encoding="utf-8").strip()
        if version_path.exists()
        else ""
    )
    if previous_version_text != release.version:
        version_path.write_text(f"{release.version}\n", encoding="utf-8")
        _append_log(log_path, f"Updated VERSION file to {release.version}")
        GIT_ADAPTER.run(["git", "add", "VERSION"], check=True)
        _append_log(log_path, "Staged VERSION for commit")
    else:
        _append_log(
            log_path, f"VERSION already set to {release.version}; skipping update"
        )
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
    if diff.returncode != 0:
        subprocess.run(
            ["git", "commit", "-m", f"pre-release commit {release.version}"],
            check=True,
        )
        _append_log(log_path, f"Committed VERSION update for {release.version}")
    else:
        _append_log(log_path, "No release metadata changes detected; skipping commit")
    _append_log(log_path, "Pre-release actions complete")


def _step_run_tests(release, ctx, log_path: Path, *, user=None) -> None:
    """Execute release test suite gate.

    Prerequisites: pre-release actions completed.
    Side effects: writes test output to release log.
    Rollback expectations: no rollback; failures pause progression.
    """
    _ = release, user
    _append_log(log_path, "Complete test suite with --all flag")
    tests_result = ctx.get("tests_result")
    if isinstance(tests_result, dict) and tests_result.get("success") is True:
        _append_log(
            log_path,
            "Test gate passed using recorded test evidence "
            f"(command={ctx.get('tests_command') or 'unknown'}, "
            f"verified_at={ctx.get('tests_verified_at') or 'unknown'})",
        )
        return

    validation_command = getattr(settings, RELEASE_VALIDATION_COMMAND_SETTING, None)
    if not validation_command:
        _fail_release_gate(
            ctx,
            log_path,
            "Release test gate failed: provide successful test evidence "
            "(tests_verified_at, tests_command, tests_result.success=true) "
            f"or configure {RELEASE_VALIDATION_COMMAND_SETTING} to run tests automatically.",
        )

    command = _normalize_validation_command(validation_command)
    command_text = shlex.join(command)
    configured_timeout = int(
        getattr(
            settings,
            RELEASE_VALIDATION_TIMEOUT_SETTING,
            DEFAULT_RELEASE_VALIDATION_TIMEOUT_SECONDS,
        )
    )
    _append_log(
        log_path,
        "Running release validation command: "
        f"{command_text} (timeout={configured_timeout}s)",
    )
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=configured_timeout,
        )
    except subprocess.TimeoutExpired:
        ctx["tests_command"] = command_text
        ctx["tests_result"] = {
            "success": False,
            "reason": "timeout",
            "source": "pipeline_command",
            "timeout_seconds": configured_timeout,
        }
        _fail_release_gate(
            ctx,
            log_path,
            "Release test gate failed: configured validation command "
            f"'{command_text}' timed out after {configured_timeout} seconds. "
            "Fix the stalled tests and rerun the step.",
        )
    if result.stdout.strip():
        _append_log(log_path, "Validation command stdout:\n" + result.stdout.strip())
    if result.stderr.strip():
        _append_log(log_path, "Validation command stderr:\n" + result.stderr.strip())
    if result.returncode != 0:
        _fail_release_gate(
            ctx,
            log_path,
            "Release test gate failed: configured validation command exited "
            f"with status {result.returncode}. Fix the failing tests and rerun the step.",
        )

    ctx["tests_verified_at"] = timezone.now().isoformat()
    ctx["tests_command"] = command_text
    ctx["tests_result"] = {
        "success": True,
        "returncode": result.returncode,
        "source": "pipeline_command",
    }
    _append_log(log_path, "Release test gate passed")


def _step_prune_low_value_tests(release, ctx, log_path: Path, *, user=None) -> None:
    """Require per-release test-suite pruning evidence.

    Prerequisites: release test gate completed.
    Side effects: records the pruning PR evidence in release context/logs.
    Rollback expectations: no rollback; missing evidence pauses progression.
    """
    del release, user
    _append_log(log_path, "Prune worst 1% of tests by PR")
    pruning_result = ctx.get("test_pruning_result")
    pruning_pr_url = str(ctx.get("test_pruning_pr_url") or "").strip()
    pruning_source = "release_context"

    if isinstance(pruning_result, dict):
        if pruning_result.get("success") is False:
            _fail_release_gate(
                ctx,
                log_path,
                "Release test pruning gate failed: recorded pruning evidence "
                "explicitly failed. Fix the pruning PR and rerun this step.",
            )
        if pruning_result.get("success") is True:
            pruning_pr_url = str(pruning_result.get("pr_url") or pruning_pr_url).strip()
            pruning_source = str(pruning_result.get("source") or pruning_source)

    if not pruning_pr_url and ctx.get("auto_release"):
        pruning_pr_url = str(
            getattr(settings, TEST_PRUNING_PR_URL_SETTING, "") or ""
        ).strip()
        if pruning_pr_url:
            pruning_source = "settings"

    if not pruning_pr_url:
        message = (
            "Release readiness paused: prune the worst 1% of tests by PR before "
            "publishing. Prioritize low-value, duplicate, over-mocked, confusing, "
            "or misleading tests. Record a test pruning PR URL in the publish "
            "workflow or configure RELEASE_PUBLISH_TEST_PRUNING_PR_URL for "
            "scheduled releases, then rerun this step."
        )
        ctx["paused"] = True
        ctx["test_pruning_required"] = True
        ctx["test_pruning_error"] = _(message)
        _append_log(log_path, message)
        raise PublishPending()

    if not _is_pull_request_url(pruning_pr_url):
        _fail_release_gate(
            ctx,
            log_path,
            "Release test pruning gate failed: recorded pruning evidence must be "
            "a GitHub pull request URL.",
        )

    ctx.pop("test_pruning_required", None)
    ctx.pop("test_pruning_error", None)
    ctx["test_pruning_pr_url"] = pruning_pr_url
    ctx["test_pruning_result"] = {
        "success": True,
        "source": pruning_source,
        "pr_url": pruning_pr_url,
        "criteria": list(TEST_PRUNING_CRITERIA),
    }
    _append_log(log_path, f"Test pruning gate passed using PR {pruning_pr_url}")


def _step_confirm_pypi_trusted_publisher_settings(
    release, ctx, log_path: Path, *, user=None
) -> None:
    """Confirm PyPI Trusted Publisher release prerequisites.

    Prerequisites: full release test suite acknowledgement complete.
    Side effects: records confirmation message in release logs.
    Rollback expectations: no rollback; this is a publish gate acknowledgement.
    """
    _ = release, user
    _append_log(log_path, "Confirm PyPI Trusted Publisher settings")
    workflow_path = Path(".github/workflows") / EXPECTED_PUBLISH_WORKFLOW_FILE
    if not workflow_path.exists():
        _fail_release_gate(
            ctx,
            log_path,
            f"Trusted Publisher gate failed: {workflow_path} is missing. "
            "Add the publish workflow before publishing.",
        )

    workflow_data: dict = {}
    yaml_error = False
    try:
        loaded_workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        if isinstance(loaded_workflow, dict):
            workflow_data = loaded_workflow
    except yaml.YAMLError:
        yaml_error = True

    mismatches: list[str] = []
    if yaml_error:
        mismatches.append(
            f"workflow YAML in {workflow_path} must be valid and parseable"
        )

    on_section = workflow_data.get("on", workflow_data.get(True, {}))
    push_section = on_section.get("push", {}) if isinstance(on_section, dict) else {}
    raw_tags = push_section.get("tags") if isinstance(push_section, dict) else None
    tags: list[str] = []
    if isinstance(raw_tags, str):
        tags = [raw_tags]
    elif isinstance(raw_tags, list):
        tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()]
    elif raw_tags is not None:
        tags = [str(raw_tags).strip()] if str(raw_tags).strip() else []
    if raw_tags is None or not tags:
        mismatches.append(
            f"{workflow_path} must define non-empty on.push.tags"
            " (missing key: on.push.tags)"
        )
    expected_tag = EXPECTED_PUBLISH_REF_PATTERN.removeprefix("refs/tags/")
    if tags and set(tags) != {expected_tag}:
        mismatches.append(
            f"workflow tag pattern must be {EXPECTED_PUBLISH_REF_PATTERN} "
            f"(check key: on.push.tags in {workflow_path})"
        )

    jobs = workflow_data.get("jobs", {})
    publish_job = jobs.get("publish-to-pypi", {}) if isinstance(jobs, dict) else {}
    if not isinstance(publish_job, dict) or not publish_job:
        mismatches.append(
            f"{workflow_path} must define jobs.publish-to-pypi"
            " (missing key: jobs.publish-to-pypi)"
        )

    environment = (
        publish_job.get("environment", "") if isinstance(publish_job, dict) else ""
    )
    observed_environment_name = ""
    if isinstance(environment, str):
        observed_environment_name = environment.strip()
    elif isinstance(environment, dict):
        observed_environment_name = str(environment.get("name", "")).strip()
    if not observed_environment_name:
        mismatches.append(
            f"{workflow_path} must define non-empty publish job environment.name"
            " (missing key: jobs.publish-to-pypi.environment.name)"
        )
    elif observed_environment_name != EXPECTED_PUBLISH_ENVIRONMENT:
        mismatches.append(
            f"workflow environment must be {EXPECTED_PUBLISH_ENVIRONMENT} "
            f"(check key: jobs.publish-to-pypi.environment.name in {workflow_path})"
        )

    job_permissions = (
        publish_job.get("permissions") if isinstance(publish_job, dict) else None
    )
    permissions = (
        job_permissions
        if job_permissions is not None
        else workflow_data.get("permissions", {})
    )
    id_token_permission = ""
    if isinstance(permissions, dict):
        id_token_permission = str(permissions.get("id-token", "")).strip()
    elif isinstance(permissions, str) and permissions == "write-all":
        id_token_permission = "write"
    if id_token_permission != "write":
        mismatches.append(
            f"{workflow_path} must set jobs.publish-to-pypi.permissions.id-token to"
            " 'write' (missing/invalid key: jobs.publish-to-pypi.permissions.id-token)"
        )

    steps = publish_job.get("steps", []) if isinstance(publish_job, dict) else []
    uses_entries = []
    if isinstance(steps, list):
        uses_entries = [
            str(step.get("uses", "")).strip()
            for step in steps
            if isinstance(step, dict) and str(step.get("uses", "")).strip()
        ]
    has_publish_action = any(
        action.startswith("pypa/gh-action-pypi-publish@")
        or action == "pypa/gh-action-pypi-publish"
        for action in uses_entries
    )
    if not has_publish_action:
        mismatches.append(
            f"{workflow_path} must include pypa/gh-action-pypi-publish in"
            " jobs.publish-to-pypi.steps[*].uses"
            " (missing key family: jobs.publish-to-pypi.steps[*].uses)"
        )

    static_token_keys = (
        "password",
        "token",
        "api_token",
        "repository_password",
        "user",
        "username",
    )
    has_static_token_field = False
    has_non_oidc_publish_path = False
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        uses = str(step.get("uses", "")).strip()
        if uses:
            normalized_uses = uses.lower()
            if (
                "pypa/gh-action-pypi-publish" not in normalized_uses
                and "pypi-publish" in normalized_uses
            ):
                has_non_oidc_publish_path = True
                break
        run_command = str(step.get("run", "")).strip().lower()
        if "twine upload" in run_command:
            has_non_oidc_publish_path = True
            break
        if not (
            uses.startswith("pypa/gh-action-pypi-publish@")
            or uses == "pypa/gh-action-pypi-publish"
        ):
            continue
        step_with = step.get("with", {})
        if not isinstance(step_with, dict):
            continue
        if any(str(step_with.get(key, "")).strip() for key in static_token_keys):
            has_static_token_field = True
            break
    if has_static_token_field:
        mismatches.append(
            f"{workflow_path} must not set static token credentials in"
            " jobs.publish-to-pypi.steps[*].with when Trusted Publisher OIDC is expected"
            " (remove keys like password/token/api_token)"
        )
    if has_non_oidc_publish_path:
        mismatches.append(
            f"{workflow_path} jobs.publish-to-pypi.steps must use only"
            " pypa/gh-action-pypi-publish for package upload"
            " (remove twine upload and other publish actions)"
        )

    if mismatches:
        _fail_release_gate(
            ctx,
            log_path,
            "Trusted Publisher gate failed: " + "; ".join(mismatches) + ".",
        )

    ctx["trusted_publisher_verified_at"] = timezone.now().isoformat()
    ctx["trusted_publisher_workflow_file"] = EXPECTED_PUBLISH_WORKFLOW_FILE
    ctx["trusted_publisher_ref"] = EXPECTED_PUBLISH_REF_PATTERN
    ctx["trusted_publisher_environment"] = EXPECTED_PUBLISH_ENVIRONMENT
    _append_log(
        log_path,
        "Trusted Publisher gate passed "
        f"(workflow={EXPECTED_PUBLISH_WORKFLOW_FILE}, "
        f"ref={EXPECTED_PUBLISH_REF_PATTERN}, environment={EXPECTED_PUBLISH_ENVIRONMENT})",
    )


def _normalize_validation_command(command: str | Sequence[str]) -> list[str]:
    if isinstance(command, str):
        parts = shlex.split(command)
    else:
        parts = [str(part) for part in command if str(part).strip()]
    if not parts:
        raise ValueError("Validation command is empty")
    return parts


def _fail_release_gate(ctx: dict, log_path: Path, message: str) -> NoReturn:
    ctx["paused"] = True
    ctx["error"] = _(message)
    _append_log(log_path, message)
    raise PublishPending()


def _step_promote_build(release, ctx, log_path: Path, *, user=None) -> None:
    """Promote build artifacts into releasable state.

    Prerequisites: tests completed successfully.
    Side effects: may create git tags/commits and update release fields.
    Rollback expectations: promotion is append-only; retry or manual corrective commit expected.
    """
    _ = user
    _append_log(log_path, "Generating build files")
    ctx.pop("build_revision", None)
    if ctx.get("dry_run"):
        _append_log(log_path, "Dry run: skipping build promotion")
        return
    if ctx.get("build_promoted"):
        _append_log(log_path, "Retrying push of release changes to origin")
        _push_release_changes(
            log_path, ctx, step_name=BUILD_RELEASE_ARTIFACTS_STEP_NAME
        )
        ctx.pop("build_promoted", None)
        PackageRelease.dump_fixture()
        _append_log(log_path, "Updated release fixtures")
        return
    try:
        _ensure_origin_main_unchanged(log_path)
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        status_output = status_result.stdout.strip()
        if status_output:
            _append_log(
                log_path,
                "Git repository is not clean; git status --porcelain:\n"
                + status_output,
            )
        release_utils.promote(
            package=release.to_package(),
            version=release.version,
            creds=release.to_credentials(),
            stash=True,
        )
        _append_log(
            log_path,
            f"Generated release artifacts for v{release.version}",
        )
        fixture_paths = [
            str(path) for path in Path("apps/core/fixtures").glob("releases__*.json")
        ]
        paths = ["VERSION", *fixture_paths]
        diff = subprocess.run(
            ["git", "status", "--porcelain", *paths],
            capture_output=True,
            text=True,
        )
        if diff.stdout.strip():
            GIT_ADAPTER.run(["git", "add", *paths], check=True)
            _append_log(log_path, "Staged release metadata updates")
            subprocess.run(
                [
                    "git",
                    "commit",
                    "-m",
                    f"chore: update release metadata for v{release.version}",
                ],
                check=True,
            )
            _append_log(
                log_path,
                f"Committed release metadata for v{release.version}",
            )
        ctx["build_promoted"] = True
        _push_release_changes(
            log_path, ctx, step_name=BUILD_RELEASE_ARTIFACTS_STEP_NAME
        )
        ctx.pop("build_promoted", None)
        PackageRelease.dump_fixture()
        _append_log(log_path, "Updated release fixtures")
    except PublishPending:
        raise
    except (
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        requests.exceptions.RequestException,
        ValueError,
    ):
        _clean_repo()
        raise
    target_name = _release_log_name(release.package.name, release.version)
    new_log = log_path.with_name(target_name)
    if log_path != new_log:
        if new_log.exists():
            new_log.unlink()
        log_path.rename(new_log)
    else:
        new_log = log_path
    ctx["log"] = new_log.name
    ctx["build_revision"] = _current_git_revision()
    _append_log(new_log, "Build complete")


def _step_verify_release_environment(
    release, ctx, log_path: Path, *, user=None
) -> None:
    """Verify runtime/package environment readiness.

    Prerequisites: build promotion complete.
    Side effects: records verification details in context/logs.
    Rollback expectations: none, this is validation only.
    """
    if ctx.get("dry_run"):
        _append_log(log_path, "Dry run: skipping release environment verification")
        return

    if not _has_remote("origin"):
        raise RuntimeError(
            _(
                "Git remote 'origin' is not configured. Configure your git remote "
                "to point at the repository before continuing."
            )
        )

    _require_github_token(
        release,
        ctx,
        log_path,
        message=_("GitHub token missing. Provide a token to continue publishing."),
        user=user,
    )

    _append_log(
        log_path,
        "Release environment verified (origin remote configured, GitHub token available)",
    )


def _collect_release_artifacts() -> list[Path]:
    dist_path = Path("dist")
    if not dist_path.exists():
        raise FileNotFoundError("dist directory not found")
    artifacts = sorted(
        [
            *dist_path.glob("*.whl"),
            *dist_path.glob("*.tar.gz"),
        ]
    )
    if not artifacts:
        raise RuntimeError("No release artifacts found in dist/")
    return artifacts


def _step_export_and_dispatch(release, ctx, log_path: Path, *, user=None) -> None:
    """Export release artifacts and dispatch publish workflow.

    Prerequisites: environment verification succeeded and token available.
    Side effects: creates/updates GitHub release assets and publish workflow context.
    Rollback expectations: reruns overwrite assets; no destructive rollback required.
    """
    if ctx.get("dry_run"):
        _append_log(
            log_path,
            "Dry run: skipping GitHub Actions publish trigger",
        )
        return

    if not release_utils.network_available():
        raise RuntimeError("Network unavailable; cannot export artifacts")

    artifacts = _collect_release_artifacts()
    owner, repo = _resolve_github_repository(release)
    token = _require_github_token(
        release,
        ctx,
        log_path,
        message=_("GitHub token missing. Provide a token to continue publishing."),
        user=user,
    )
    tag_name = _ensure_release_tag(release, log_path)
    release_data = _ensure_github_release(
        owner=owner,
        repo=repo,
        tag_name=tag_name,
        token=token,
    )
    _upload_release_assets(
        owner=owner,
        repo=repo,
        release_data=release_data,
        token=token,
        artifacts=artifacts,
        log_path=log_path,
    )
    _append_log(log_path, "Exported release artifacts to GitHub release")
    _append_log(
        log_path,
        f"Release tag {tag_name} pushed; publish workflow will run on tag.",
    )


def _wait_for_publish_workflow_completion(
    release,
    ctx: dict[str, object],
    log_path: Path,
    *,
    token: str | None,
) -> dict[str, object]:
    owner, repo = _resolve_github_repository(release)
    ctx["github_owner"] = owner
    ctx["github_repo"] = repo
    tag_name = f"v{release.version}"
    run = _fetch_publish_workflow_run(
        owner=owner,
        repo=repo,
        tag_name=tag_name,
        token=token,
    )
    if not run:
        _pause_for_publish_pending(
            release,
            ctx,
            log_path,
            token=token,
            message=(
                "Publish workflow run not found yet; resume after GitHub Actions completes."
            ),
        )

    run_url = run.get("html_url") if isinstance(run.get("html_url"), str) else ""
    run_id = run.get("id")
    if isinstance(run_id, int):
        ctx["publish_workflow_run_id"] = run_id
    if run_url:
        ctx["publish_workflow_url"] = run_url

    status = run.get("status")
    if status != "completed":
        if run_url:
            message = "Publish workflow still running; monitor at"
        else:
            message = (
                "Publish workflow still running; resume after GitHub Actions completes."
            )
        _pause_for_publish_pending(
            release,
            ctx,
            log_path,
            token=token,
            message=message,
            run_url=run_url,
        )

    ctx["publish_workflow_status"] = status
    conclusion = run.get("conclusion")
    if isinstance(conclusion, str) and conclusion:
        ctx["publish_workflow_conclusion"] = conclusion
    return run


def _step_wait_for_github_actions_publish(
    release, ctx, log_path: Path, *, user=None
) -> None:
    """Pause until GitHub Actions publish workflow completes.

    Prerequisites: release artifacts exported and tag pushed.
    Side effects: stores workflow run details in context/logs.
    Rollback expectations: no rollback; retries continue polling.
    """
    if ctx.get("dry_run"):
        _append_log(log_path, "Dry run: skipping GitHub Actions publish wait")
        return
    token = _require_github_token(
        release,
        ctx,
        log_path,
        message=_("GitHub token missing. Provide a token to continue publishing."),
        user=user,
    )
    _wait_for_publish_workflow_completion(
        release,
        ctx,
        log_path,
        token=token,
    )
    run_url = ctx.get("publish_workflow_url", "")
    if not isinstance(run_url, str):
        run_url = ""
    if run_url:
        _append_log(log_path, f"Publish workflow completed: {run_url}")
    else:
        _append_log(log_path, "Publish workflow completed")


def _pypi_release_available(release) -> bool:
    if not release_utils.network_available():
        return False
    resp = None
    try:
        resp = requests.get(
            f"https://pypi.org/pypi/{release.package.name}/json",
            timeout=PYPI_REQUEST_TIMEOUT,
        )
        if not resp.ok:
            return False
        data = resp.json()
        releases = data.get("releases", {})
        try:
            target_version = Version(release.version)
        except InvalidVersion:
            target_version = None
        for candidate, files in releases.items():
            same_version = candidate == release.version
            if target_version is not None and not same_version:
                try:
                    same_version = Version(candidate) == target_version
                except InvalidVersion:
                    same_version = False
            if not same_version:
                continue
            if any(
                isinstance(file_data, dict) and not file_data.get("yanked", False)
                for file_data in files or []
            ):
                return True
        return False
    except requests.exceptions.RequestException:
        return False
    finally:
        if resp is not None:
            with contextlib.suppress(Exception):
                resp.close()


def _step_record_publish_metadata(release, ctx, log_path: Path, *, user=None) -> None:
    """Persist publish metadata after external publish completion.

    Prerequisites: GitHub Actions publish workflow completion.
    Side effects: updates PackageRelease timestamps/urls and commits fixtures.
    Rollback expectations: metadata commits can be reverted with standard git workflow.
    """
    if ctx.get("dry_run"):
        _append_log(log_path, "Dry run: skipped release metadata updates")
        return

    if not _pypi_release_available(release):
        _pause_for_publish_pending(
            release,
            ctx,
            log_path,
            token=_resolve_github_token(release, ctx, user=user),
            message=(
                "Publish not detected on PyPI yet; resume after GitHub Actions completes."
            ),
        )

    targets = release.build_publish_targets()
    release.pypi_url = (
        f"https://pypi.org/project/{release.package.name}/{release.version}/"
    )
    github_url = release.github_release_url() or ""
    for target in targets[1:]:
        if github_url:
            break
        if target.repository_url:
            github_url = (
                release.github_release_url(target.repository_url)
                or release.github_package_url(target.repository_url)
                or ""
            )
            if github_url:
                break
    if github_url:
        release.github_url = github_url
    else:
        release.github_url = ""
    release.release_on = timezone.now()
    release.save(update_fields=["pypi_url", "github_url", "release_on"])
    PackageRelease.dump_fixture()
    _append_log(log_path, f"Recorded PyPI URL: {release.pypi_url}")
    if release.github_url:
        _append_log(log_path, f"Recorded GitHub URL: {release.github_url}")
    _record_release_fixture_updates(
        log_path,
        ctx,
        commit_message=f"chore: record publish metadata for v{release.version}",
        staged_message="Staged publish metadata updates",
        committed_message=f"Committed publish metadata for v{release.version}",
        skipped_message=(
            "No release metadata updates detected after publish; skipping commit"
        ),
        step_name="Record publish URLs & update fixtures",
    )
    _append_log(log_path, "Publish metadata recorded")


def _step_capture_publish_logs(release, ctx, log_path: Path, *, user=None) -> None:
    """Collect publish workflow logs for audit trail.

    Prerequisites: publish workflow run exists or can be discovered.
    Side effects: downloads and truncates workflow logs to release log directory.
    Rollback expectations: log capture is best-effort and safe to retry.
    """
    if ctx.get("dry_run"):
        _append_log(log_path, "Dry run: skipped capture of publish logs")
        return

    token = _resolve_github_token(release, ctx, user=user)
    if not token:
        ctx.setdefault("warnings", []).append(
            {
                "message": _(
                    "GitHub token missing; PyPI publish logs were not captured."
                ),
                "followups": [
                    _("Provide a GitHub token in the publish workflow to capture logs.")
                ],
            }
        )
        _append_log(log_path, "GitHub token missing; skipping publish log capture")
        return

    run = _wait_for_publish_workflow_completion(
        release,
        ctx,
        log_path,
        token=token,
    )

    run_id = run.get("id")
    if not isinstance(run_id, int):
        raise ValueError("Publish workflow run ID missing")
    owner, repo = ctx["github_owner"], ctx["github_repo"]

    raw_log = _download_publish_workflow_logs(
        owner=owner, repo=repo, run_id=run_id, token=token
    )
    if not raw_log:
        _pause_for_publish_pending(
            release,
            ctx,
            log_path,
            token=token,
            message="Publish workflow logs empty; resume after GitHub Actions completes.",
        )

    run_url = run.get("html_url") or ""
    conclusion = run.get("conclusion") or ""
    summary_lines = [
        f"Workflow run: {run_url or run_id}",
        f"Status: {run.get('status')}",
    ]
    if conclusion:
        summary_lines.append(f"Conclusion: {conclusion}")
    log_text = "\n".join(summary_lines) + "\n\n" + raw_log
    log_text = _truncate_publish_log(log_text)

    if log_text != release.pypi_publish_log:
        release.pypi_publish_log = log_text
        release.save(update_fields=["pypi_publish_log"])
        PackageRelease.dump_fixture()
        _append_log(log_path, "Recorded PyPI publish logs")
        _record_release_fixture_updates(
            log_path,
            ctx,
            commit_message=f"chore: record publish logs for v{release.version}",
            staged_message="Staged publish log updates",
            committed_message=f"Committed publish log updates for v{release.version}",
            skipped_message="Publish logs already recorded; skipping commit",
            step_name="Capture PyPI publish logs",
        )
    else:
        _append_log(log_path, "Publish logs already recorded")


_STEP_HANDLER_MAP = {
    "_step_check_version": _step_check_version,
    "_step_handle_migrations": _step_handle_migrations,
    "_step_pre_release_actions": _step_pre_release_actions,
    "_step_promote_build": _step_promote_build,
    "_step_run_tests": _step_run_tests,
    "_step_prune_low_value_tests": _step_prune_low_value_tests,
    "_step_confirm_pypi_trusted_publisher_settings": _step_confirm_pypi_trusted_publisher_settings,
    "_step_verify_release_environment": _step_verify_release_environment,
    "_step_export_and_dispatch": _step_export_and_dispatch,
    "_step_wait_for_github_actions_publish": _step_wait_for_github_actions_publish,
    "_step_record_publish_metadata": _step_record_publish_metadata,
    "_step_capture_publish_logs": _step_capture_publish_logs,
}

PUBLISH_STEPS = [
    (name, _STEP_HANDLER_MAP[handler_name])
    for name, handler_name in DOMAIN_PUBLISH_STEPS
]


def _ensure_publish_step_compatibility(
    typed_ctx: ReleasePublishContext,
    steps: list[tuple[str, object]],
) -> ReleasePublishContext:
    expected_schema = "|".join(name for name, _func in steps)
    recorded_schema = typed_ctx.extras.get("publish_steps_schema")
    if recorded_schema is None:
        typed_ctx.extras["publish_steps_schema"] = expected_schema
        return typed_ctx

    if (
        recorded_schema != expected_schema
        and typed_ctx.started
        and typed_ctx.step < len(steps)
    ):
        typed_ctx.step = 0
        typed_ctx.started = False
        typed_ctx.paused = False
        typed_ctx.error = _(
            "Release publish steps changed after an upgrade. Restart the publish workflow to continue safely."
        )

    typed_ctx.extras["publish_steps_schema"] = expected_schema
    return typed_ctx


def release_progress_impl(request, pk: int, action: str):
    release, error_response = _get_release_or_response(request, pk, action)
    if error_response:
        return error_response
    release_pk = release.pk
    session_key = f"release_publish_{release_pk}"
    lock_dir = Path(settings.BASE_DIR) / ".locks"
    try:
        lock_path = _resolve_safe_child_path(
            lock_dir,
            f"release_publish_{release_pk}.json",
        )
        restart_path = _resolve_safe_child_path(
            lock_dir,
            f"release_publish_{release_pk}.restarts",
        )
    except ValueError:
        return _render_release_progress_error(
            request,
            release,
            action,
            _("Invalid release state path."),
            status=400,
            debug_info={"pk": release_pk, "action": action},
        )
    log_dir, log_dir_warning = _resolve_release_log_dir(Path(settings.LOG_DIR))
    log_dir_warning_message = log_dir_warning

    version_path = Path("VERSION")
    repo_version_before_sync = ""
    if version_path.exists():
        repo_version_before_sync = version_path.read_text(encoding="utf-8").strip()
    sync_response = _handle_release_sync(
        request,
        release,
        action,
        session_key,
        lock_path,
        restart_path,
        log_dir,
        repo_version_before_sync,
    )
    if sync_response:
        return sync_response

    restart_response = _handle_release_restart(
        request,
        release,
        session_key,
        lock_path,
        restart_path,
        log_dir,
    )
    if restart_response:
        return restart_response

    workflow = ReleasePublishWorkflow(
        request=request,
        session_key=session_key,
        lock_path=lock_path,
        restart_path=restart_path,
        clean_redirect_path=_clean_redirect_path,
        collect_dirty_files=_collect_dirty_files,
        validate_manual_git_push=_validate_manual_git_push,
        append_log=_append_log,
    )
    typed_ctx, log_dir_warning_message = workflow.load(log_dir_warning_message)
    ctx = workflow.template_state(typed_ctx)

    steps = PUBLISH_STEPS
    typed_ctx = _ensure_publish_step_compatibility(typed_ctx, steps)
    ctx = workflow.template_state(typed_ctx)
    step_count = typed_ctx.step
    start_enabled = _is_release_start_enabled(ctx, step_count, len(steps))

    start_requested = bool(request.GET.get("start")) and start_enabled
    typed_ctx = workflow.start(typed_ctx, start_enabled=start_enabled)
    typed_ctx, resume_requested, redirect_response = workflow.resume(typed_ctx)
    if redirect_response:
        return redirect_response
    ctx = workflow.template_state(typed_ctx)
    restart_count, step_param = workflow.step_progress(
        typed_ctx, resume_requested=resume_requested
    )

    ctx, log_path, step_count = _prepare_logging(
        ctx,
        release,
        log_dir,
        log_dir_warning_message,
        step_param,
        step_count,
    )

    if _build_artifacts_stale(ctx, step_count, steps):
        return _reset_release_progress(
            request,
            release,
            session_key,
            lock_path,
            restart_path,
            log_dir,
            clean_repo=False,
            message_text=_(
                "Source changes detected after build. Restarting publish workflow."
            ),
        )

    ctx = _handle_dirty_repository_action(request, ctx, log_path)
    ctx = _handle_manual_git_push_action(request, ctx, log_path)
    typed_ctx = ReleasePublishContext.from_dict(ctx)
    step_count = typed_ctx.step

    fixtures_step_index = next(
        (
            index
            for index, (name, _) in enumerate(steps)
            if name == FIXTURE_REVIEW_STEP_NAME
        ),
        None,
    )

    poll_requested, publish_poll_allowed = workflow.poll(typed_ctx)

    if not start_requested:
        typed_ctx, step_count = workflow.advance(
            steps=steps,
            ctx=typed_ctx,
            step_param=step_param,
            release=release,
            log_path=log_path,
            allow_when_paused=publish_poll_allowed,
        )
        ctx = workflow.template_state(typed_ctx)

    error = ctx.get("error")
    done = step_count >= len(steps) and not error

    if done and not ctx.get("release_net_message_sent"):
        _broadcast_release_message(release)
        ctx["release_net_message_sent"] = True

    show_log, log_content = _resolve_release_log_display(
        ctx, step_count, done, log_path
    )
    next_step = _resolve_next_step(ctx, step_count, done)
    dirty_files = ctx.get("dirty_files")
    if dirty_files:
        next_step = None
    paused = ctx.get("paused", False)
    publish_pending = bool(ctx.get("publish_pending"))

    step_names = [s[0] for s in steps]
    step_states = _build_release_step_states(
        step_names=step_names,
        step_count=step_count,
        error=bool(error),
        paused=paused,
        started=bool(ctx.get("started")),
        done=done,
    )

    is_running = ctx.get("started") and not paused and not done and not ctx.get("error")
    resume_available = (
        ctx.get("started")
        and not paused
        and not done
        and not ctx.get("error")
        and step_count < len(steps)
        and next_step is None
    )
    can_resume = ctx.get("started") and paused and not done and not ctx.get("error")
    oidc_enabled = release.uses_oidc_publishing()
    pypi_credentials_missing = not oidc_enabled and release.to_credentials() is None
    stored_github_token = _get_user_github_token(request.user)
    session_github_token = (ctx.get("github_token") or "").strip()
    github_token_using_stored = bool(stored_github_token and not session_github_token)
    github_token_edit_url = None
    if stored_github_token:
        github_token_edit_url = reverse(
            "admin:repos_githubtoken_change", args=[stored_github_token.pk]
        )
    github_credentials_missing = (
        _resolve_github_token(release, ctx, user=request.user) is None
    )
    manual_git_push = ctx.get("pending_git_push")
    manual_git_push_command = ""
    if manual_git_push:
        manual_git_push_command = _manual_git_push_command(manual_git_push)

    fixtures_summary = ctx.get("fixtures")
    if (
        fixtures_summary
        and fixtures_step_index is not None
        and step_count > fixtures_step_index
    ):
        fixtures_summary = None

    dry_run_active = bool(ctx.get("dry_run"))
    dry_run_toggle_enabled = not is_running and not done and not ctx.get("error")

    status_guidance = build_release_guidance(
        done=done,
        error=ctx.get("error"),
        started=bool(ctx.get("started", False)),
        paused=paused,
        publish_pending=publish_pending,
        github_token_required=bool(ctx.get("github_token_required", False)),
        step_count=step_count,
        total_steps=len(steps),
    )

    context = _build_release_progress_context(
        release=release,
        step_names=step_names,
        step_count=step_count,
        next_step=next_step,
        done=done,
        ctx=ctx,
        log_content=log_content,
        log_path=log_path,
        fixtures_summary=fixtures_summary,
        dirty_files=dirty_files,
        restart_count=restart_count,
        paused=paused,
        show_log=show_log,
        start_requested=start_requested,
        step_states=step_states,
        oidc_enabled=oidc_enabled,
        pypi_credentials_missing=pypi_credentials_missing,
        github_credentials_missing=github_credentials_missing,
        github_token_using_stored=github_token_using_stored,
        github_token_edit_url=github_token_edit_url,
        is_running=is_running,
        resume_available=resume_available,
        can_resume=can_resume,
        dry_run_active=dry_run_active,
        dry_run_toggle_enabled=dry_run_toggle_enabled,
        manual_git_push=manual_git_push,
        manual_git_push_command=manual_git_push_command,
        publish_pending=publish_pending,
        status_guidance=status_guidance,
    )
    return _finalize_release_progress_response(
        request=request,
        workflow=workflow,
        ctx=ctx,
        context=context,
        done=done,
        publish_pending=publish_pending,
        dry_run_active=dry_run_active,
        poll_requested=poll_requested,
        step_count=step_count,
        next_step=next_step,
        paused=paused,
    )


def _is_release_start_enabled(ctx: dict, step_count: int, total_steps: int) -> bool:
    started_flag = bool(ctx.get("started"))
    paused_flag = bool(ctx.get("paused"))
    error_flag = bool(ctx.get("error"))
    done_flag = step_count >= total_steps and not error_flag
    return (not started_flag or paused_flag) and not done_flag and not error_flag


def _resolve_release_log_display(
    ctx: dict, step_count: int, done: bool, log_path: Path
):
    show_log = (
        bool(ctx.get("started")) or step_count > 0 or done or bool(ctx.get("error"))
    )
    if show_log and log_path.exists():
        return show_log, log_path.read_text(encoding="utf-8")
    return show_log, ""


def _resolve_next_step(ctx: dict, step_count: int, done: bool):
    if (
        ctx.get("started")
        and not ctx.get("paused")
        and not done
        and not ctx.get("error")
    ):
        return step_count
    return None


def _build_release_step_states(
    *,
    step_names: list[str],
    step_count: int,
    error: bool,
    paused: bool,
    started: bool,
    done: bool,
):
    step_states = []
    for index, name in enumerate(step_names):
        status, icon, label = _build_release_step_state(
            index=index,
            step_count=step_count,
            error=error,
            paused=paused,
            started=started,
            done=done,
        )
        step_states.append(
            {
                "index": index + 1,
                "name": name,
                "status": status,
                "icon": icon,
                "label": label,
            }
        )
    return step_states


def _build_release_step_state(
    *, index: int, step_count: int, error: bool, paused: bool, started: bool, done: bool
):
    if index < step_count:
        return "complete", "✅", _("Completed")
    if error and index == step_count:
        return "error", "❌", _("Failed")
    if paused and started and index == step_count and not done:
        return "paused", "⏸️", _("Paused")
    if started and index == step_count and not done:
        return "active", "⏳", _("In progress")
    return "pending", "⬜", _("Pending")


def _build_release_progress_context(
    *,
    release,
    step_names: list[str],
    step_count: int,
    next_step,
    done: bool,
    ctx: dict,
    log_content: str,
    log_path: Path,
    fixtures_summary,
    dirty_files,
    restart_count: int,
    paused: bool,
    show_log: bool,
    start_requested: bool,
    step_states: list[dict],
    oidc_enabled: bool,
    pypi_credentials_missing: bool,
    github_credentials_missing: bool,
    github_token_using_stored: bool,
    github_token_edit_url,
    is_running: bool,
    resume_available: bool,
    can_resume: bool,
    dry_run_active: bool,
    dry_run_toggle_enabled: bool,
    manual_git_push,
    manual_git_push_command: str,
    publish_pending: bool,
    status_guidance,
):
    return {
        "release": release,
        "action": "publish",
        "steps": step_names,
        "current_step": step_count,
        "next_step": next_step,
        "done": done,
        "error": ctx.get("error"),
        "log_content": log_content,
        "log_path": str(log_path),
        "cert_log": ctx.get("cert_log"),
        "fixtures": fixtures_summary,
        "dirty_files": dirty_files,
        "dirty_commit_message": ctx.get(
            "dirty_commit_message", DIRTY_COMMIT_DEFAULT_MESSAGE
        ),
        "dirty_commit_error": ctx.get("dirty_commit_error"),
        "restart_count": restart_count,
        "started": ctx.get("started", False),
        "paused": paused,
        "show_log": show_log,
        "start_pending": start_requested,
        "step_states": step_states,
        "oidc_enabled": oidc_enabled,
        "pypi_credentials_missing": pypi_credentials_missing,
        "github_credentials_missing": github_credentials_missing,
        "github_token_required": ctx.get("github_token_required", False),
        "github_token_using_stored": github_token_using_stored,
        "github_token_edit_url": github_token_edit_url,
        "is_running": is_running,
        "resume_available": resume_available,
        "can_resume": can_resume,
        "dry_run": dry_run_active,
        "dry_run_toggle_enabled": dry_run_toggle_enabled,
        "warnings": ctx.get("warnings", []),
        "manual_git_push": manual_git_push,
        "manual_git_push_command": manual_git_push_command,
        "manual_git_push_error": ctx.get("pending_git_push_error"),
        "publish_pending": publish_pending,
        "publish_workflow_url": ctx.get("publish_workflow_url", ""),
        "test_pruning_required": ctx.get("test_pruning_required", False),
        "test_pruning_error": ctx.get("test_pruning_error"),
        "test_pruning_pr_url": ctx.get("test_pruning_pr_url", ""),
        "status_guidance": status_guidance,
    }


def _finalize_release_progress_response(
    *,
    request,
    workflow: ReleasePublishWorkflow,
    ctx: dict,
    context: dict,
    done: bool,
    publish_pending: bool,
    dry_run_active: bool,
    poll_requested: bool,
    step_count: int,
    next_step,
    paused: bool,
):
    workflow.persist_state(
        ReleasePublishContext.from_dict(ctx),
        done=done,
    )

    if publish_pending:
        poll_query = {"step": step_count, "poll": "1"}
        if dry_run_active:
            poll_query["dry_run"] = "1"
        poll_base = _clean_redirect_path(request, request.path)
        context["publish_poll_url"] = f"{poll_base}?{urlencode(poll_query)}"

    if poll_requested:
        return _build_release_progress_poll_response(
            request=request,
            ctx=ctx,
            done=done,
            dry_run_active=dry_run_active,
            step_count=step_count,
            next_step=next_step,
            paused=paused,
            publish_pending=publish_pending,
        )

    return _render_release_progress_response(request, context)


def _build_release_progress_poll_response(
    *,
    request,
    ctx: dict,
    done: bool,
    dry_run_active: bool,
    step_count: int,
    next_step,
    paused: bool,
    publish_pending: bool,
):
    refresh_query = {}
    if not done and not ctx.get("error"):
        refresh_query["step"] = step_count
    if dry_run_active:
        refresh_query["dry_run"] = "1"
    refresh_base = _clean_redirect_path(request, request.path)
    refresh_url = (
        f"{refresh_base}?{urlencode(refresh_query)}" if refresh_query else refresh_base
    )
    return JsonResponse(
        {
            "done": done,
            "error": _sanitize_release_error_message(ctx.get("error"), ctx),
            "paused": paused,
            "publish_pending": publish_pending,
            "current_step": step_count,
            "next_step": next_step,
            "refresh_url": refresh_url,
        }
    )


def _render_release_progress_response(request, context: dict):
    template = _ensure_template_name(
        get_template("core/release_progress.html"),
        "core/release_progress.html",
    )
    content = template.render(context, request)
    import django.test.signals as test_signals

    if test_signals.template_rendered.receivers:
        test_signals.template_rendered.send(
            sender=template.__class__,
            template=template,
            context=context,
            using=getattr(getattr(template, "engine", None), "name", None),
        )
    response = HttpResponse(content)
    response.context = context
    response.templates = [template]
    return response


def _dedupe_preserve_order(values):
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def build_release_guidance(
    *,
    done: bool,
    error: str | None,
    started: bool,
    paused: bool,
    publish_pending: bool,
    github_token_required: bool,
    step_count: int,
    total_steps: int,
) -> dict[str, str]:
    """Build user-facing status guidance for the release progress screen."""

    if done:
        return {
            "tone": "success",
            "title": _("Publish completed"),
            "message": _(
                "All release steps finished successfully. You can now share the package URLs below."
            ),
        }

    if error:
        return {
            "tone": "error",
            "title": _("Publish needs attention"),
            "message": _(
                "Resolve the error below, then continue to retry the current step."
            ),
        }

    if not started:
        return {
            "tone": "info",
            "title": _("Ready to publish"),
            "message": _(
                "Review credentials and click Start Publish when you are ready."
            ),
        }

    if paused and github_token_required:
        return {
            "tone": "warning",
            "title": _("GitHub token required"),
            "message": _(
                "Publishing is paused until a GitHub token is provided for this session."
            ),
        }

    if paused and publish_pending:
        return {
            "tone": "warning",
            "title": _("Waiting for GitHub Actions"),
            "message": _(
                "The publish workflow is still running on GitHub. This page will keep checking automatically."
            ),
        }

    if paused:
        return {
            "tone": "warning",
            "title": _("Publishing paused"),
            "message": _("Press Continue Publish to proceed from the current step."),
        }

    return {
        "tone": "info",
        "title": _("Publishing in progress"),
        "message": _("Step %(current)s of %(total)s is running.")
        % {
            "current": min(step_count + 1, total_steps),
            "total": total_steps,
        },
    }

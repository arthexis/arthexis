from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import subprocess
import uuid
import zipfile
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import urlencode, urlparse

import requests
from packaging.version import InvalidVersion, Version
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import get_template
from django.test import signals
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.http import url_has_allowed_host_and_scheme

from apps.loggers.paths import select_log_dir
from apps.nodes.models import NetMessage, Node
from apps.release import release as release_utils
from apps.release.models import PackageRelease
from utils import revision

logger = logging.getLogger(__name__)

PYPI_REQUEST_TIMEOUT = 10

DIRTY_COMMIT_DEFAULT_MESSAGE = "chore: commit pending changes"

DIRTY_STATUS_LABELS = {
    "A": _("Added"),
    "C": _("Copied"),
    "D": _("Deleted"),
    "M": _("Modified"),
    "R": _("Renamed"),
    "U": _("Updated"),
    "??": _("Untracked"),
}

SENSITIVE_CONTEXT_KEYS = {"github_token"}


def _sanitize_release_context(ctx: dict) -> dict:
    return {key: value for key, value in ctx.items() if key not in SENSITIVE_CONTEXT_KEYS}


def _sanitize_release_error_message(error: str | None, ctx: dict) -> str | None:
    if not error:
        return None

    sanitized = str(error)
    for key in SENSITIVE_CONTEXT_KEYS:
        value = ctx.get(key)
        if value:
            sanitized = sanitized.replace(str(value), "[redacted]")
    return sanitized


def _store_release_context(request, session_key: str, ctx: dict) -> None:
    request.session[session_key] = _sanitize_release_context(ctx)


def _persist_release_context(
    request, session_key: str, ctx: dict, lock_path: Path
) -> None:
    _store_release_context(request, session_key, ctx)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(ctx), encoding="utf-8")
    lock_path.chmod(0o600)


class DirtyRepository(Exception):
    """Raised when the Git workspace has uncommitted changes."""


class PublishPending(Exception):
    """Raised when publish metadata updates must wait for external publishing."""


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def _release_log_name(package_name: str, version: str) -> str:
    return f"pr.{package_name}.v{version}.log"


def _ensure_log_directory(path: Path) -> tuple[bool, OSError | None]:
    """Return whether ``path`` is writable along with the triggering error."""

    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, exc

    probe = path / f".permcheck_{uuid.uuid4().hex}"
    try:
        with probe.open("w", encoding="utf-8") as fh:
            fh.write("")
    except OSError as exc:
        return False, exc
    else:
        try:
            probe.unlink()
        except OSError:
            pass
        return True, None


def _resolve_release_log_dir(preferred: Path) -> tuple[Path, str | None]:
    """Return a writable log directory for the release publish flow."""

    writable, error = _ensure_log_directory(preferred)
    if writable:
        return preferred, None

    logger.warning(
        "Release log directory %s is not writable: %s", preferred, error
    )

    env_override = os.environ.pop("ARTHEXIS_LOG_DIR", None)
    fallback = select_log_dir(Path(settings.BASE_DIR))
    if env_override is not None:
        if Path(env_override) == fallback:
            os.environ["ARTHEXIS_LOG_DIR"] = env_override
        else:
            os.environ["ARTHEXIS_LOG_DIR"] = str(fallback)

    if fallback == preferred:
        if error:
            raise error
        raise PermissionError(f"Release log directory {preferred} is not writable")

    fallback_writable, fallback_error = _ensure_log_directory(fallback)
    if not fallback_writable:
        raise fallback_error or PermissionError(
            f"Release log directory {fallback} is not writable"
        )

    settings.LOG_DIR = fallback
    warning = (
        f"Release log directory {preferred} is not writable; using {fallback}"
    )
    logger.warning(warning)
    return fallback, warning


def _resolve_github_token(release: PackageRelease, ctx: dict) -> str | None:
    token = (ctx.get("github_token") or "").strip()
    if token:
        return token
    return release.get_github_token()


def _require_github_token(
    release: PackageRelease,
    ctx: dict,
    log_path: Path,
    *,
    message: str,
) -> str:
    token = _resolve_github_token(release, ctx)
    if token:
        return token
    ctx["paused"] = True
    ctx["github_token_required"] = True
    _append_log(log_path, message)
    raise PublishPending()


def _render_release_progress_error(
    request,
    release: PackageRelease | None,
    action: str,
    message: str,
    *,
    status: int = 400,
    debug_info: dict | None = None,
) -> HttpResponse:
    """Return a simple error response for the release progress view."""

    debug_info = debug_info or {}
    logger.error(
        "Release progress error for %s (%s): %s; debug=%s",
        release or "unknown release",
        action,
        message,
        debug_info,
    )
    debug_payload = None
    if settings.DEBUG and debug_info:
        debug_payload = json.dumps(debug_info, indent=2, sort_keys=True)
    return render(
        request,
        "core/release_progress_error.html",
        {
            "release": release,
            "action": action,
            "message": str(message),
            "debug_info": debug_payload,
            "status_code": status,
        },
        status=status,
    )


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


def _ensure_template_name(template, name: str):
    """Ensure the template has a name attribute for debugging hooks."""

    if not getattr(template, "name", None):
        template.name = name
    return template


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
            _(
                "Another release already exists for %(package)s %(version)s."
            )
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
        except Exception:
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
    ctx = request.session.get(session_key)
    lock_ctx = None
    new_ctx = False
    if lock_path.exists():
        try:
            lock_ctx = json.loads(lock_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Failed to load release context from lock file %s: %s",
                lock_path,
                exc,
            )
            lock_ctx = None
    if ctx is None and lock_ctx is not None:
        ctx = lock_ctx
    elif ctx is None:
        ctx = {"step": 0}
        new_ctx = True
    elif lock_ctx is not None and "github_token" in lock_ctx:
        ctx.setdefault("github_token", lock_ctx["github_token"])
    if new_ctx and restart_path.exists():
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
            ctx["github_token"] = token
            ctx.pop("github_token_required", None)
            if (
                ctx.get("paused")
                and not ctx.get("dirty_files")
                and not ctx.get("pending_git_push")
            ):
                ctx["paused"] = False
            messages.success(
                request,
                _("GitHub token stored for this publish session."),
            )
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
        return ctx, False, redirect(
            _clean_redirect_path(request, request.path),
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
        except Exception:
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
        (index for index, (name, _) in enumerate(steps) if name == "Build release artifacts"),
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
    except Exception:
        node = None
    node_label = str(node) if node else "unknown"
    body = f"@ {node_label}"
    try:
        NetMessage.broadcast(subject=subject, body=body)
    except Exception:
        logger.exception(
            "Failed to broadcast release Net Message",
            extra={"subject": subject, "body": body},
        )


def _handle_dirty_repository_action(request, ctx: dict, log_path: Path):
    dirty_action = request.GET.get("dirty_action")
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
            message = request.GET.get("dirty_message", "").strip()
            if not message:
                message = ctx.get("dirty_commit_message") or DIRTY_COMMIT_DEFAULT_MESSAGE
            ctx["dirty_commit_message"] = message
            try:
                subprocess.run(["git", "add", "--all"], check=True)
                subprocess.run(["git", "commit", "-m", message], check=True)
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
                    _("Committed pending changes: %(message)s")
                    % {"message": message},
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
    action = request.GET.get("manual_push_action")
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
    error = ctx.get("error")

    was_paused = bool(ctx.get("paused"))

    if (
        ctx.get("started")
        and (not ctx.get("paused") or allow_when_paused)
        and step_param is not None
        and not error
        and step_count < len(steps)
    ):
        try:
            to_run = int(step_param)
        except (TypeError, ValueError):
            ctx["error"] = _("An internal error occurred while running this step.")
            _append_log(log_path, "Invalid step parameter; aborting publish step.")
            _persist_release_context(request, session_key, ctx, lock_path)
            return ctx, step_count
        if to_run == step_count:
            name, func = steps[to_run]
            try:
                func(release, ctx, log_path, user=request.user)
            except DirtyRepository:
                pass
            except PublishPending:
                pass
            except Exception as exc:  # pragma: no cover - best effort logging
                _append_log(log_path, f"{name} failed: {exc}")
                ctx["error"] = str(exc)
                ctx.pop("publish_pending", None)
                _persist_release_context(request, session_key, ctx, lock_path)
            else:
                step_count += 1
                ctx["step"] = step_count
                if allow_when_paused and was_paused and not ctx.get("publish_pending"):
                    ctx["paused"] = False
                if not ctx.get("publish_pending"):
                    ctx.pop("publish_pending", None)
                _persist_release_context(request, session_key, ctx, lock_path)

    return ctx, step_count


def _sync_with_origin_main(log_path: Path) -> None:
    """Ensure the current branch is rebased onto ``origin/main``."""

    if not _has_remote("origin"):
        _append_log(log_path, "No git remote configured; skipping sync with origin/main")
        return

    try:
        subprocess.run(["git", "fetch", "origin", "main"], check=True)
        _append_log(log_path, "Fetched latest changes from origin/main")
        subprocess.run(["git", "rebase", "origin/main"], check=True)
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

        raise Exception("Rebase onto main failed") from exc


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
    proc = subprocess.run(args, check=True, capture_output=True, text=True)
    return (proc.stdout or "").strip()


def _current_git_revision() -> str:
    try:
        return _git_stdout(["git", "rev-parse", "HEAD"])
    except Exception:
        return ""


def _working_tree_dirty() -> bool:
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return False
    return bool((status.stdout or "").strip())


def _has_remote(remote: str) -> bool:
    proc = subprocess.run(
        ["git", "remote"],
        check=True,
        capture_output=True,
        text=True,
    )
    remotes = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return remote in remotes


def _current_branch() -> str | None:
    branch = _git_stdout(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if branch == "HEAD":
        return None
    return branch


def _has_upstream(branch: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _collect_dirty_files() -> list[dict[str, str]]:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    dirty: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        status_code = line[:2]
        status = status_code.strip() or status_code
        path = line[3:]
        dirty.append(
            {
                "path": path,
                "status": status,
                "status_label": DIRTY_STATUS_LABELS.get(status, status),
            }
        )
    return dirty


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
    return (exc.stderr or exc.stdout or str(exc)).strip() or str(exc)


def _parse_github_repository(repo_url: str) -> tuple[str, str] | None:
    repo_url = (repo_url or "").strip()
    if not repo_url:
        return None
    if repo_url.startswith("git@"):
        if "github.com" not in repo_url:
            return None
        _, _, path = repo_url.partition("github.com:")
        path = path.strip("/")
    else:
        parsed = urlparse(repo_url)
        if "github.com" not in parsed.netloc.lower():
            return None
        path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def _resolve_github_repository(release: PackageRelease) -> tuple[str, str]:
    repo_url = release.package.repository_url or ""
    parsed = _parse_github_repository(repo_url)
    if parsed:
        return parsed
    remote_url = release_utils._git_remote_url()
    if remote_url:
        parsed = _parse_github_repository(remote_url)
        if parsed:
            return parsed
    raise Exception("GitHub repository URL is required to export artifacts")


def _github_headers(token: str | None) -> dict[str, str]:
    if not token:
        raise Exception("GitHub token is required to export artifacts")
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github_request(
    method: str,
    url: str,
    *,
    token: str | None,
    expected_status: set[int],
    **kwargs,
) -> requests.Response:
    headers = kwargs.pop("headers", {})
    headers.update(_github_headers(token))
    response = requests.request(method, url, headers=headers, **kwargs)
    if response.status_code not in expected_status:
        detail = response.text.strip()
        raise Exception(
            f"GitHub API request failed ({response.status_code}): {detail}"
        )
    return response


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
    release_utils._push_tag(tag_name, release.to_package())
    _append_log(log_path, f"Pushed git tag {tag_name} to origin")
    return tag_name


def _ensure_github_release(
    *,
    owner: str,
    repo: str,
    tag_name: str,
    token: str | None,
) -> dict[str, object]:
    release_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag_name}"
    response = _github_request(
        "get",
        release_url,
        token=token,
        expected_status={200, 404},
    )
    if response.status_code == 404:
        create_url = f"https://api.github.com/repos/{owner}/{repo}/releases"
        response = _github_request(
            "post",
            create_url,
            token=token,
            expected_status={201},
            json={"tag_name": tag_name, "name": tag_name},
        )
    elif response.status_code != 200:
        detail = response.text.strip()
        raise Exception(
            f"GitHub release lookup failed ({response.status_code}): {detail}"
        )
    data = response.json()
    if not isinstance(data, dict):
        raise Exception("GitHub release response was not a JSON object")
    return data


def _upload_release_assets(
    *,
    owner: str,
    repo: str,
    release_data: dict[str, object],
    token: str | None,
    artifacts: Sequence[Path],
    log_path: Path,
) -> None:
    assets = release_data.get("assets") or []
    existing_assets: dict[str, int] = {}
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = asset.get("name")
            asset_id = asset.get("id")
            if isinstance(name, str) and isinstance(asset_id, int):
                existing_assets[name] = asset_id

    release_id = release_data.get("id")
    if not isinstance(release_id, int):
        raise Exception("GitHub release ID missing")

    for artifact in artifacts:
        name = artifact.name
        existing_id = existing_assets.get(name)
        if existing_id:
            delete_url = (
                "https://api.github.com/repos/"
                f"{owner}/{repo}/releases/assets/{existing_id}"
            )
            _github_request(
                "delete",
                delete_url,
                token=token,
                expected_status={204},
            )
            _append_log(log_path, f"Removed existing GitHub asset {name}")

        upload_url = (
            "https://uploads.github.com/repos/"
            f"{owner}/{repo}/releases/{release_id}/assets"
            f"?name={name}"
        )
        with artifact.open("rb") as handle:
            _github_request(
                "post",
                upload_url,
                token=token,
                expected_status={201},
                headers={"Content-Type": "application/octet-stream"},
                data=handle,
            )
        _append_log(log_path, f"Uploaded GitHub release asset {name}")


def _fetch_publish_workflow_run(
    *,
    owner: str,
    repo: str,
    tag_name: str,
    token: str | None,
) -> dict[str, object] | None:
    runs_url = (
        f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/publish.yml/runs"
    )
    try:
        tag_sha = _git_stdout(["git", "rev-parse", f"{tag_name}^{{}}"])
    except Exception:
        tag_sha = ""

    def _get_runs(params: dict[str, object]) -> list[dict[str, object]] | None:
        response = _github_request(
            "get",
            runs_url,
            token=token,
            expected_status={200},
            params=params,
        )
        payload = response.json()
        runs = payload.get("workflow_runs")
        if not isinstance(runs, list):
            return None
        return runs

    runs = _get_runs({"event": "push", "branch": tag_name, "per_page": 5})
    if not runs:
        runs = _get_runs({"event": "push", "per_page": 5})
    if not isinstance(runs, list) or not runs:
        return None
    for run in runs:
        if run.get("head_branch") == tag_name:
            return run
    if tag_sha:
        for run in runs:
            if run.get("head_sha") == tag_sha:
                return run
    return None


def _append_publish_workflow_status(
    release: PackageRelease,
    log_path: Path,
    *,
    token: str | None,
    message: str,
) -> None:
    run_url = ""
    try:
        owner, repo = _resolve_github_repository(release)
        run = _fetch_publish_workflow_run(
            owner=owner,
            repo=repo,
            tag_name=f"v{release.version}",
            token=token,
        )
    except Exception:
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


def _download_publish_workflow_logs(
    *,
    owner: str,
    repo: str,
    run_id: int,
    token: str | None,
) -> str:
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/logs"
    response = _github_request(
        "get",
        url,
        token=token,
        expected_status={200},
        allow_redirects=True,
        timeout=30,
    )
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    sections: list[str] = []
    for name in sorted(archive.namelist()):
        if not name.endswith(".txt"):
            continue
        data = archive.read(name).decode("utf-8", errors="replace")
        sections.append(f"--- {name} ---\n{data}")
    return "\n\n".join(sections)


MAX_PYPI_PUBLISH_LOG_SIZE = 50000


def _truncate_publish_log(
    log_text: str, *, limit: int = MAX_PYPI_PUBLISH_LOG_SIZE
) -> str:
    if len(log_text) <= limit:
        return log_text
    trimmed = log_text[-limit:]
    return f"[truncated; last {limit} of {len(log_text)} chars]\n{trimmed}"


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
        subprocess.run(["git", "add", *fixture_paths], check=True)
        _append_log(log_path, staged_message)
        subprocess.run(["git", "commit", "-m", commit_message], check=True)
        _append_log(log_path, committed_message)
        _push_release_changes(log_path, ctx, step_name=step_name)
    else:
        _append_log(log_path, skipped_message)


def _git_authentication_missing(exc: subprocess.CalledProcessError) -> bool:
    message = (exc.stderr or exc.stdout or "").strip().lower()
    if not message:
        return False
    auth_markers = [
        "could not read username",
        "authentication failed",
        "fatal: authentication failed",
        "terminal prompts disabled",
    ]
    return any(marker in message for marker in auth_markers)


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
            raise PublishPending()
        _append_log(
            log_path, f"Failed to push release changes to origin: {details}"
        )
        raise Exception("Failed to push release changes") from exc

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
        details = (
            getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or str(exc)
        ).strip()
        if details:
            _append_log(log_path, f"Failed to verify origin/main status: {details}")
        else:  # pragma: no cover - defensive fallback
            _append_log(log_path, "Failed to verify origin/main status")
        raise Exception("Unable to verify origin/main status") from exc

    if origin_main != merge_base:
        _append_log(log_path, "origin/main advanced during release; restart required")
        raise Exception("origin/main changed during release; restart required")

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


def _step_check_version(release, ctx, log_path: Path, *, user=None) -> None:
    sync_error: Optional[Exception] = None
    retry_sync = False
    try:
        _sync_with_origin_main(log_path)
    except Exception as exc:
        sync_error = exc

    if not release_utils._git_clean():
        dirty_entries = _collect_dirty_files()
        files = [entry["path"] for entry in dirty_entries]
        fixture_files = [
            f
            for f in files
            if "fixtures" in Path(f).parts and Path(f).suffix == ".json"
        ]
        version_dirty = "VERSION" in files
        allowed_dirty_files = set(fixture_files)
        if version_dirty:
            allowed_dirty_files.add("VERSION")

        if files and len(allowed_dirty_files) == len(files):
            summary = []
            for f in fixture_files:
                path = Path(f)
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    count = 0
                    models: list[str] = []
                else:
                    if isinstance(data, list):
                        count = len(data)
                        models = sorted(
                            {
                                obj.get("model", "")
                                for obj in data
                                if isinstance(obj, dict)
                            }
                        )
                    elif isinstance(data, dict):
                        count = 1
                        models = [data.get("model", "")]
                    else:  # pragma: no cover - unexpected structure
                        count = 0
                        models = []
                summary.append({"path": f, "count": count, "models": models})

            ctx["fixtures"] = summary
            commit_paths = [*fixture_files]
            if version_dirty:
                commit_paths.append("VERSION")

            log_fragments = []
            if fixture_files:
                log_fragments.append("fixtures " + ", ".join(fixture_files))
            if version_dirty:
                log_fragments.append("VERSION")
            details = ", ".join(log_fragments) if log_fragments else "changes"
            _append_log(
                log_path,
                f"Committing release prep changes: {details}",
            )
            subprocess.run(["git", "add", *commit_paths], check=True)

            if version_dirty and fixture_files:
                commit_message = "chore: update version and fixtures"
            elif version_dirty:
                commit_message = "chore: update version"
            else:
                commit_message = "chore: update fixtures"

            subprocess.run(["git", "commit", "-m", commit_message], check=True)
            _append_log(
                log_path,
                f"Release prep changes committed ({commit_message})",
            )
            ctx.pop("dirty_files", None)
            ctx.pop("dirty_commit_error", None)
            retry_sync = True
        else:
            ctx["dirty_files"] = dirty_entries
            ctx.setdefault("dirty_commit_message", DIRTY_COMMIT_DEFAULT_MESSAGE)
            ctx.pop("fixtures", None)
            ctx.pop("dirty_commit_error", None)
            if dirty_entries:
                details = ", ".join(entry["path"] for entry in dirty_entries)
            else:
                details = ""
            message = "Git repository has uncommitted changes"
            if details:
                message += f": {details}"
            if ctx.get("dirty_log_message") != message:
                _append_log(log_path, message)
                ctx["dirty_log_message"] = message
            raise DirtyRepository()
    else:
        ctx.pop("dirty_files", None)
        ctx.pop("dirty_commit_error", None)
        ctx.pop("dirty_log_message", None)

    if retry_sync and sync_error is not None:
        try:
            _sync_with_origin_main(log_path)
        except Exception as exc:
            sync_error = exc
        else:
            sync_error = None

    previous_repo_version = getattr(release, "_repo_version_before_sync", "")

    if sync_error is not None:
        raise sync_error

    version_path = Path("VERSION")
    if version_path.exists():
        current = version_path.read_text(encoding="utf-8").strip()
        if current:
            current_clean = PackageRelease.strip_dev_suffix(current) or "0.0.0"
            if Version(release.version) < Version(current_clean):
                raise Exception(
                    f"Version {release.version} is older than existing {current}"
                )

    _append_log(log_path, f"Checking if version {release.version} exists on PyPI")
    if release_utils.network_available():
        resp = None
        try:
            resp = requests.get(
                f"https://pypi.org/pypi/{release.package.name}/json",
                timeout=PYPI_REQUEST_TIMEOUT,
            )
            if resp.ok:
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

                    has_available_files = any(
                        isinstance(file_data, dict)
                        and not file_data.get("yanked", False)
                        for file_data in files or []
                    )
                    if has_available_files:
                        raise Exception(
                            f"Version {release.version} already on PyPI"
                        )
        except Exception as exc:
            # network errors should be logged but not crash
            if "already on PyPI" in str(exc):
                raise
            _append_log(log_path, f"PyPI check failed: {exc}")
        else:
            _append_log(
                log_path,
                f"Version {release.version} not published on PyPI",
            )
        finally:
            if resp is not None:
                close = getattr(resp, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()
    else:
        _append_log(log_path, "Network unavailable, skipping PyPI check")


def _step_handle_migrations(release, ctx, log_path: Path, *, user=None) -> None:
    _append_log(log_path, "Freeze, squash and approve migrations")
    _append_log(log_path, "Migration review acknowledged (manual step)")


def _step_pre_release_actions(release, ctx, log_path: Path, *, user=None) -> None:
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
        formatted = ", ".join(
            _format_path(path) for path in staged_release_fixtures
        )
        _append_log(log_path, "Staged release fixtures " + formatted)
    version_path = Path("VERSION")
    previous_version_text = (
        version_path.read_text(encoding="utf-8").strip()
        if version_path.exists()
        else ""
    )
    repo_version_before_sync = getattr(
        release, "_repo_version_before_sync", previous_version_text
    )
    if previous_version_text != release.version:
        version_path.write_text(f"{release.version}\n", encoding="utf-8")
        _append_log(log_path, f"Updated VERSION file to {release.version}")
        subprocess.run(["git", "add", "VERSION"], check=True)
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
        _append_log(
            log_path, "No release metadata changes detected; skipping commit"
        )
    _append_log(log_path, "Pre-release actions complete")


def _step_run_tests(release, ctx, log_path: Path, *, user=None) -> None:
    _append_log(log_path, "Complete test suite with --all flag")
    _append_log(log_path, "Test suite completion acknowledged")


def _step_promote_build(release, ctx, log_path: Path, *, user=None) -> None:
    _append_log(log_path, "Generating build files")
    ctx.pop("build_revision", None)
    if ctx.get("dry_run"):
        _append_log(log_path, "Dry run: skipping build promotion")
        return
    if ctx.get("build_promoted"):
        _append_log(log_path, "Retrying push of release changes to origin")
        _push_release_changes(
            log_path, ctx, step_name="Build release artifacts"
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
                "Git repository is not clean; git status --porcelain:\n" + status_output,
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
        from glob import glob

        paths = ["VERSION", *glob("apps/core/fixtures/releases__*.json")]
        diff = subprocess.run(
            ["git", "status", "--porcelain", *paths],
            capture_output=True,
            text=True,
        )
        if diff.stdout.strip():
            subprocess.run(["git", "add", *paths], check=True)
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
            log_path, ctx, step_name="Build release artifacts"
        )
        ctx.pop("build_promoted", None)
        PackageRelease.dump_fixture()
        _append_log(log_path, "Updated release fixtures")
    except PublishPending:
        raise
    except Exception:
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
        message=_(
            "GitHub token missing. Provide a token to continue publishing."
        ),
    )

    _append_log(
        log_path,
        "Release environment verified (origin remote configured, GitHub token available)",
    )


def _collect_release_artifacts() -> list[Path]:
    dist_path = Path("dist")
    if not dist_path.exists():
        raise Exception("dist directory not found")
    artifacts = sorted(
        [
            *dist_path.glob("*.whl"),
            *dist_path.glob("*.tar.gz"),
        ]
    )
    if not artifacts:
        raise Exception("No release artifacts found in dist/")
    return artifacts


def _step_export_and_dispatch(release, ctx, log_path: Path, *, user=None) -> None:
    if ctx.get("dry_run"):
        _append_log(
            log_path,
            "Dry run: skipping GitHub Actions publish trigger",
        )
        return

    if not release_utils.network_available():
        raise Exception("Network unavailable; cannot export artifacts")

    artifacts = _collect_release_artifacts()
    owner, repo = _resolve_github_repository(release)
    token = _require_github_token(
        release,
        ctx,
        log_path,
        message=_(
            "GitHub token missing. Provide a token to continue publishing."
        ),
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


def _pypi_release_available(release) -> bool:
    if not release_utils.network_available():
        return False
    try:
        resp = requests.get(
            f"https://pypi.org/pypi/{release.package.name}/json",
            timeout=PYPI_REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException:
        return False
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
            isinstance(file_data, dict)
            and not file_data.get("yanked", False)
            for file_data in files or []
        ):
            return True
    return False


def _step_record_publish_metadata(release, ctx, log_path: Path, *, user=None) -> None:
    if ctx.get("dry_run"):
        _append_log(log_path, "Dry run: skipped release metadata updates")
        return

    if not _pypi_release_available(release):
        ctx["paused"] = True
        ctx["publish_pending"] = True
        _append_publish_workflow_status(
            release,
            log_path,
            token=_resolve_github_token(release, ctx),
            message="Publish not detected on PyPI yet; resume after GitHub Actions completes.",
        )
        raise PublishPending()

    targets = release.build_publish_targets()
    release.pypi_url = (
        f"https://pypi.org/project/{release.package.name}/{release.version}/"
    )
    github_url = ""
    for target in targets[1:]:
        if target.repository_url:
            parsed = urlparse(target.repository_url)
            host = parsed.hostname or ""
            if host == "github.com" or host.endswith(".github.com"):
                github_url = release.github_package_url() or ""
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
        step_name="Record publish metadata",
    )
    _append_log(log_path, "Publish metadata recorded")


def _step_capture_publish_logs(release, ctx, log_path: Path, *, user=None) -> None:
    if ctx.get("dry_run"):
        _append_log(log_path, "Dry run: skipped capture of publish logs")
        return

    token = _resolve_github_token(release, ctx)
    if not token:
        ctx.setdefault("warnings", []).append(
            {
                "message": _(
                    "GitHub token missing; PyPI publish logs were not captured."
                ),
                "followups": [
                    _(
                        "Provide a GitHub token in the publish workflow to capture logs."
                    )
                ],
            }
        )
        _append_log(log_path, "GitHub token missing; skipping publish log capture")
        return

    owner, repo = _resolve_github_repository(release)
    tag_name = f"v{release.version}"
    run = _fetch_publish_workflow_run(
        owner=owner,
        repo=repo,
        tag_name=tag_name,
        token=token,
    )
    if not run:
        ctx["paused"] = True
        ctx["publish_pending"] = True
        _append_publish_workflow_status(
            release,
            log_path,
            token=token,
            message="Publish workflow run not found yet; resume after GitHub Actions completes.",
        )
        raise PublishPending()

    status = run.get("status")
    if status != "completed":
        ctx["paused"] = True
        ctx["publish_pending"] = True
        run_url = run.get("html_url") if isinstance(run.get("html_url"), str) else ""
        if run_url:
            _append_log(
                log_path,
                f"Publish workflow still running; monitor at {run_url}",
            )
        else:
            _append_log(
                log_path,
                "Publish workflow still running; resume after GitHub Actions completes.",
            )
        raise PublishPending()

    run_id = run.get("id")
    if not isinstance(run_id, int):
        raise ValueError("Publish workflow run ID missing")

    raw_log = _download_publish_workflow_logs(
        owner=owner, repo=repo, run_id=run_id, token=token
    )
    if not raw_log:
        ctx["paused"] = True
        ctx["publish_pending"] = True
        _append_publish_workflow_status(
            release,
            log_path,
            token=token,
            message="Publish workflow logs empty; resume after GitHub Actions completes.",
        )
        raise PublishPending()

    run_url = run.get("html_url") or ""
    conclusion = run.get("conclusion") or ""
    summary_lines = [
        f"Workflow run: {run_url or run_id}",
        f"Status: {status}",
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


FIXTURE_REVIEW_STEP_NAME = "Freeze, squash and approve migrations"


PUBLISH_STEPS = [
    ("Check version number availability", _step_check_version),
    (FIXTURE_REVIEW_STEP_NAME, _step_handle_migrations),
    ("Execute pre-release actions", _step_pre_release_actions),
    ("Build release artifacts", _step_promote_build),
    ("Complete test suite with --all flag", _step_run_tests),
    ("Verify release environment", _step_verify_release_environment),
    (
        "Export artifacts and trigger GitHub Actions publish",
        _step_export_and_dispatch,
    ),
    ("Record publish metadata", _step_record_publish_metadata),
    ("Capture PyPI publish logs", _step_capture_publish_logs),
]


@staff_member_required
def release_progress(request, pk: int, action: str):
    release, error_response = _get_release_or_response(request, pk, action)
    if error_response:
        return error_response
    try:
        safe_pk = int(pk)
    except (TypeError, ValueError):
        return _render_release_progress_error(
            request,
            None,
            action,
            _("Invalid release ID provided."),
            status=400,
            debug_info={"pk": pk},
        )
    session_key = f"release_publish_{safe_pk}"
    lock_dir = Path(settings.BASE_DIR) / ".locks"
    lock_path = lock_dir / f"release_publish_{safe_pk}.json"
    restart_path = lock_dir / f"release_publish_{safe_pk}.restarts"
    log_dir, log_dir_warning = _resolve_release_log_dir(Path(settings.LOG_DIR))
    log_dir_warning_message = log_dir_warning

    version_path = Path("VERSION")
    repo_version_before_sync = ""
    if version_path.exists():
        repo_version_before_sync = version_path.read_text(encoding="utf-8").strip()
    setattr(release, "_repo_version_before_sync", repo_version_before_sync)

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

    ctx, log_dir_warning_message = _load_release_context(
        request,
        session_key,
        lock_path,
        restart_path,
        log_dir_warning_message,
    )

    steps = PUBLISH_STEPS
    total_steps = len(steps)
    step_count = ctx.get("step", 0)
    started_flag = bool(ctx.get("started"))
    paused_flag = bool(ctx.get("paused"))
    error_flag = bool(ctx.get("error"))
    done_flag = step_count >= total_steps and not error_flag
    start_enabled = (not started_flag or paused_flag) and not done_flag and not error_flag

    start_requested = bool(request.GET.get("start")) and start_enabled
    ctx, resume_requested, redirect_response = _update_publish_controls(
        request,
        ctx,
        start_enabled,
        session_key,
        lock_path,
    )
    if redirect_response:
        return redirect_response
    restart_count, step_param = _prepare_step_progress(
        request, ctx, restart_path, resume_requested
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

    fixtures_step_index = next(
        (
            index
            for index, (name, _) in enumerate(steps)
            if name == FIXTURE_REVIEW_STEP_NAME
        ),
        None,
    )

    poll_requested = request.GET.get("poll") == "1"
    publish_poll_allowed = poll_requested and ctx.get("publish_pending")

    if not start_requested:
        ctx, step_count = _run_release_step(
            request,
            steps,
            ctx,
            step_param,
            step_count,
            release,
            log_path,
            session_key,
            lock_path,
            allow_when_paused=publish_poll_allowed,
        )

    error = ctx.get("error")
    done = step_count >= len(steps) and not error

    if done and not ctx.get("release_net_message_sent"):
        _broadcast_release_message(release)
        ctx["release_net_message_sent"] = True

    show_log = ctx.get("started") or step_count > 0 or done or ctx.get("error")
    if show_log and log_path.exists():
        log_content = log_path.read_text(encoding="utf-8")
    else:
        log_content = ""
    next_step = (
        step_count
        if ctx.get("started")
        and not ctx.get("paused")
        and not done
        and not ctx.get("error")
        else None
    )
    dirty_files = ctx.get("dirty_files")
    if dirty_files:
        next_step = None
    paused = ctx.get("paused", False)
    publish_pending = bool(ctx.get("publish_pending"))

    step_names = [s[0] for s in steps]
    step_states = []
    for index, name in enumerate(step_names):
        if index < step_count:
            status = "complete"
            icon = "✅"
            label = _("Completed")
        elif error and index == step_count:
            status = "error"
            icon = "❌"
            label = _("Failed")
        elif paused and ctx.get("started") and index == step_count and not done:
            status = "paused"
            icon = "⏸️"
            label = _("Paused")
        elif ctx.get("started") and index == step_count and not done:
            status = "active"
            icon = "⏳"
            label = _("In progress")
        else:
            status = "pending"
            icon = "⬜"
            label = _("Pending")
        step_states.append(
            {
                "index": index + 1,
                "name": name,
                "status": status,
                "icon": icon,
                "label": label,
            }
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
    github_credentials_missing = _resolve_github_token(release, ctx) is None
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

    context = {
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
        "dirty_commit_message": ctx.get("dirty_commit_message", DIRTY_COMMIT_DEFAULT_MESSAGE),
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
    }
    if done or ctx.get("error"):
        _store_release_context(request, session_key, ctx)
        if lock_path.exists():
            lock_path.unlink()
    else:
        _persist_release_context(request, session_key, ctx, lock_path)
    if publish_pending:
        poll_query = {"step": step_count, "poll": "1"}
        if dry_run_active:
            poll_query["dry_run"] = "1"
        poll_base = _clean_redirect_path(request, request.path)
        context["publish_poll_url"] = f"{poll_base}?{urlencode(poll_query)}"
    if poll_requested:
        refresh_query = {}
        if not done and not ctx.get("error"):
            refresh_query["step"] = step_count
        if dry_run_active:
            refresh_query["dry_run"] = "1"
        refresh_base = _clean_redirect_path(request, request.path)
        refresh_url = (
            f"{refresh_base}?{urlencode(refresh_query)}"
            if refresh_query
            else refresh_base
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
    template = _ensure_template_name(
        get_template("core/release_progress.html"),
        "core/release_progress.html",
    )
    content = template.render(context, request)
    signals.template_rendered.send(
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

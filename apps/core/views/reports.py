from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import uuid
from pathlib import Path
from typing import Optional, Sequence

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.loader import get_template
from django.test import signals
from django.utils import timezone
from django.utils.translation import gettext as _
from django.urls import NoReverseMatch, reverse

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


class ApprovalRequired(Exception):
    """Raised when release manager approval is required before continuing."""


class DirtyRepository(Exception):
    """Raised when the Git workspace has uncommitted changes."""


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
    content = str(message)
    if settings.DEBUG and debug_info:
        content = f"{content}\n{json.dumps(debug_info, indent=2, sort_keys=True)}"
    return HttpResponse(content, status=status)


def _sync_release_with_revision(release: PackageRelease) -> tuple[bool, str]:
    """Align the release metadata with the current repository revision.

    Returns a tuple of (updated, previous_version).
    """

    version_path = Path("VERSION")
    previous_version = release.version
    updated = False
    if version_path.exists():
        current_version = version_path.read_text(encoding="utf-8").strip()
        if "+" in current_version:
            normalized_version = PackageRelease.normalize_version(current_version)
            if normalized_version != current_version:
                version_path.write_text(normalized_version + "\n", encoding="utf-8")
                current_version = normalized_version
        if current_version and current_version != release.version:
            release.version = current_version
            release.revision = revision.get_revision()
            release.save(update_fields=["version", "revision"])
            updated = True
    return updated, previous_version


def _ensure_template_name(template, name: str):
    """Ensure the template has a name attribute for debugging hooks."""

    if not getattr(template, "name", None):
        template.name = name
    return template


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

    updated, previous_version = _sync_release_with_revision(release)
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
    return redirect(request.path)


def _load_release_context(
    request,
    session_key: str,
    lock_path: Path,
    restart_path: Path,
    log_dir_warning_message: str | None,
):
    ctx = request.session.get(session_key)
    if ctx is None and lock_path.exists():
        try:
            ctx = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:
            ctx = {"step": 0}
    if ctx is None:
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
    credentials_ready: bool,
):
    ctx["dry_run"] = bool(ctx.get("dry_run"))

    if request.GET.get("set_dry_run") is not None:
        if start_enabled:
            ctx["dry_run"] = bool(request.GET.get("dry_run"))
            request.session[session_key] = ctx
        return ctx, False, redirect(request.path)

    if request.GET.get("start"):
        if start_enabled:
            ctx["dry_run"] = bool(request.GET.get("dry_run"))
        ctx["started"] = True
        ctx["paused"] = False

    if (
        ctx.get("awaiting_approval")
        and not ctx.get("approval_credentials_missing")
        and credentials_ready
    ):
        if request.GET.get("approve"):
            ctx["release_approval"] = "approved"
        if request.GET.get("reject"):
            ctx["release_approval"] = "rejected"

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
):
    error = ctx.get("error")

    if (
        ctx.get("started")
        and not ctx.get("paused")
        and step_param is not None
        and not error
        and step_count < len(steps)
    ):
        to_run = int(step_param)
        if to_run == step_count:
            name, func = steps[to_run]
            try:
                func(release, ctx, log_path, user=request.user)
            except ApprovalRequired:
                pass
            except DirtyRepository:
                pass
            except Exception as exc:  # pragma: no cover - best effort logging
                _append_log(log_path, f"{name} failed: {exc}")
                ctx["error"] = str(exc)
                request.session[session_key] = ctx
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text(json.dumps(ctx), encoding="utf-8")
            else:
                step_count += 1
                ctx["step"] = step_count
                request.session[session_key] = ctx
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text(json.dumps(ctx), encoding="utf-8")

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


def _format_subprocess_error(exc: subprocess.CalledProcessError) -> str:
    return (exc.stderr or exc.stdout or str(exc)).strip() or str(exc)


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


def _push_release_changes(log_path: Path) -> bool:
    """Push release commits to ``origin`` and log the outcome."""

    if not _has_remote("origin"):
        _append_log(
            log_path, "No git remote configured; skipping push of release changes"
        )
        return False

    try:
        branch = _current_branch()
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
            _append_log(
                log_path,
                "Authentication is required to push release changes to origin; skipping push",
            )
            if details:
                _append_log(log_path, details)
            return False
        _append_log(
            log_path, f"Failed to push release changes to origin: {details}"
        )
        raise Exception("Failed to push release changes") from exc

    _append_log(log_path, "Pushed release changes to origin")
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
    from packaging.version import InvalidVersion, Version

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

    from packaging.version import InvalidVersion, Version

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
    from packaging.version import InvalidVersion, Version

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
    release_fixture_paths = sorted(
        Path("apps/core/fixtures").glob("releases__*.json")
    )
    if release_fixture_paths:
        subprocess.run(
            ["git", "add", *[str(path) for path in release_fixture_paths]],
            check=True,
        )
        staged_release_fixtures = release_fixture_paths
        formatted = ", ".join(_format_path(path) for path in release_fixture_paths)
        _append_log(log_path, "Staged release fixtures " + formatted)
    version_path = Path("VERSION")
    previous_version_text = ""
    if version_path.exists():
        previous_version_text = version_path.read_text(encoding="utf-8").strip()
    repo_version_before_sync = getattr(
        release, "_repo_version_before_sync", previous_version_text
    )
    version_path.write_text(f"{release.version}\n", encoding="utf-8")
    _append_log(log_path, f"Updated VERSION file to {release.version}")
    subprocess.run(["git", "add", "VERSION"], check=True)
    _append_log(log_path, "Staged VERSION for commit")
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
    if diff.returncode != 0:
        subprocess.run(
            ["git", "commit", "-m", f"pre-release commit {release.version}"],
            check=True,
        )
        _append_log(log_path, f"Committed VERSION update for {release.version}")
    else:
        _append_log(
            log_path, "No changes detected for VERSION; skipping commit"
        )
        subprocess.run(["git", "reset", "HEAD", "VERSION"], check=False)
        _append_log(log_path, "Unstaged VERSION file")
        for path in staged_release_fixtures:
            subprocess.run(["git", "reset", "HEAD", str(path)], check=False)
            _append_log(log_path, f"Unstaged release fixture {_format_path(path)}")
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
            creds=release.to_credentials(user=user),
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
        _push_release_changes(log_path)
        PackageRelease.dump_fixture()
        _append_log(log_path, "Updated release fixtures")
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


def _step_release_manager_approval(
    release, ctx, log_path: Path, *, user=None
) -> None:
    auto_release = bool(ctx.get("auto_release"))
    creds = release.to_credentials(user=user)
    if creds is None:
        ctx.pop("release_approval", None)
        if not ctx.get("approval_credentials_missing"):
            _append_log(log_path, "Release manager publishing credentials missing")
        ctx["approval_credentials_missing"] = True
        ctx["awaiting_approval"] = True
        raise ApprovalRequired()

    missing_before = ctx.pop("approval_credentials_missing", None)
    if missing_before:
        ctx.pop("awaiting_approval", None)
    if auto_release:
        ctx.pop("release_approval", None)
        ctx.pop("awaiting_approval", None)
        ctx.pop("approval_credentials_missing", None)
        if not ctx.get("auto_release_approval_logged"):
            _append_log(log_path, "Scheduled release automatically approved")
            ctx["auto_release_approval_logged"] = True
        return
    decision = ctx.get("release_approval")
    if decision == "approved":
        ctx.pop("release_approval", None)
        ctx.pop("awaiting_approval", None)
        ctx.pop("approval_credentials_missing", None)
        _append_log(log_path, "Release manager approved release")
        return
    if decision == "rejected":
        ctx.pop("release_approval", None)
        ctx.pop("awaiting_approval", None)
        ctx.pop("approval_credentials_missing", None)
        _append_log(log_path, "Release manager rejected release")
        raise RuntimeError(
            _("Release manager rejected the release. Restart required."),
        )
    if not ctx.get("awaiting_approval"):
        ctx["awaiting_approval"] = True
        _append_log(log_path, "Awaiting release manager approval")
    else:
        ctx["awaiting_approval"] = True
    raise ApprovalRequired()


def _step_publish(release, ctx, log_path: Path, *, user=None) -> None:
    if ctx.get("dry_run"):
        test_repository_url = os.environ.get(
            "PYPI_TEST_REPOSITORY_URL", "https://test.pypi.org/legacy/"
        )
        test_creds = release.to_credentials(user=user)
        if not (test_creds and test_creds.has_auth()):
            test_creds = release_utils.Credentials(
                token=os.environ.get("PYPI_TEST_API_TOKEN"),
                username=os.environ.get("PYPI_TEST_USERNAME"),
                password=os.environ.get("PYPI_TEST_PASSWORD"),
            )
            if not test_creds.has_auth():
                test_creds = None
        target = release_utils.RepositoryTarget(
            name="Test PyPI",
            repository_url=(test_repository_url or None),
            credentials=test_creds,
            verify_availability=False,
        )
        label = target.repository_url or target.name
        dist_path = Path("dist")
        if not dist_path.exists():
            _append_log(log_path, "Dry run: building distribution artifacts")
            package = release.to_package()
            version_path = (
                Path(package.version_path)
                if package.version_path
                else Path("VERSION")
            )
            original_version = (
                version_path.read_text(encoding="utf-8")
                if version_path.exists()
                else None
            )
            pyproject_path = Path("pyproject.toml")
            original_pyproject = (
                pyproject_path.read_text(encoding="utf-8")
                if pyproject_path.exists()
                else None
            )
            try:
                release_utils.build(
                    package=package,
                    version=release.version,
                    creds=release.to_credentials(user=user),
                    dist=True,
                    tests=False,
                    twine=False,
                    git=False,
                    tag=False,
                    stash=True,
                )
            except release_utils.ReleaseError as exc:
                _append_log(
                    log_path,
                    f"Dry run: failed to prepare distribution artifacts ({exc})",
                )
                raise
            finally:
                if original_version is None:
                    if version_path.exists():
                        version_path.unlink()
                else:
                    version_path.write_text(original_version, encoding="utf-8")
                if original_pyproject is None:
                    if pyproject_path.exists():
                        pyproject_path.unlink()
                else:
                    pyproject_path.write_text(original_pyproject, encoding="utf-8")
        _append_log(log_path, f"Dry run: uploading distribution to {label}")
        release_utils.publish(
            package=release.to_package(),
            version=release.version,
            creds=target.credentials or release.to_credentials(user=user),
            repositories=[target],
        )
        _append_log(log_path, "Dry run: skipped release metadata updates")
        return

    targets = release.build_publish_targets(user=user)
    repo_labels = []
    for target in targets:
        label = target.name
        if target.repository_url:
            label = f"{label} ({target.repository_url})"
        repo_labels.append(label)
    if repo_labels:
        _append_log(
            log_path,
            "Uploading distribution"
            if len(repo_labels) == 1
            else "Uploading distribution to: " + ", ".join(repo_labels),
        )
    else:
        _append_log(log_path, "Uploading distribution")
    publish_warning: release_utils.PostPublishWarning | None = None
    try:
        release_utils.publish(
            package=release.to_package(),
            version=release.version,
            creds=release.to_credentials(user=user),
            repositories=targets,
        )
    except release_utils.PostPublishWarning as warning:
        publish_warning = warning

    if publish_warning is not None:
        message = str(publish_warning)
        followups = _dedupe_preserve_order(publish_warning.followups)
        warning_entries = ctx.setdefault("warnings", [])
        if not any(entry.get("message") == message for entry in warning_entries):
            entry: dict[str, object] = {"message": message}
            if followups:
                entry["followups"] = followups
            warning_entries.append(entry)
        _append_log(log_path, message)
        for note in followups:
            _append_log(log_path, f"Follow-up: {note}")
    release.pypi_url = (
        f"https://pypi.org/project/{release.package.name}/{release.version}/"
    )
    github_url = ""
    for target in targets[1:]:
        if target.repository_url and "github.com" in target.repository_url:
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
    fixture_paths = [
        str(path) for path in Path("apps/core/fixtures").glob("releases__*.json")
    ]
    if fixture_paths:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", *fixture_paths],
            capture_output=True,
            text=True,
            check=True,
        )
        if status.stdout.strip():
            subprocess.run(["git", "add", *fixture_paths], check=True)
            _append_log(log_path, "Staged publish metadata updates")
            commit_message = f"chore: record publish metadata for v{release.version}"
            subprocess.run(["git", "commit", "-m", commit_message], check=True)
            _append_log(
                log_path, f"Committed publish metadata for v{release.version}"
            )
            _push_release_changes(log_path)
        else:
            _append_log(
                log_path,
                "No release metadata updates detected after publish; skipping commit",
            )
    _append_log(log_path, "Upload complete")


FIXTURE_REVIEW_STEP_NAME = "Freeze, squash and approve migrations"


PUBLISH_STEPS = [
    ("Check version number availability", _step_check_version),
    (FIXTURE_REVIEW_STEP_NAME, _step_handle_migrations),
    ("Execute pre-release actions", _step_pre_release_actions),
    ("Build release artifacts", _step_promote_build),
    ("Complete test suite with --all flag", _step_run_tests),
    ("Get Release Manager Approval", _step_release_manager_approval),
    ("Upload final build to PyPI", _step_publish),
]


@staff_member_required
def release_progress(request, pk: int, action: str):
    release, error_response = _get_release_or_response(request, pk, action)
    if error_response:
        return error_response
    session_key = f"release_publish_{pk}"
    lock_dir = Path(settings.BASE_DIR) / ".locks"
    lock_path = lock_dir / f"release_publish_{pk}.json"
    restart_path = lock_dir / f"release_publish_{pk}.restarts"
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

    manager = release.release_manager or release.package.release_manager
    credentials_ready = bool(release.to_credentials(user=request.user))
    if credentials_ready and ctx.get("approval_credentials_missing"):
        ctx.pop("approval_credentials_missing", None)

    ctx, resume_requested, redirect_response = _update_publish_controls(
        request,
        ctx,
        start_enabled,
        session_key,
        credentials_ready,
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

    fixtures_step_index = next(
        (
            index
            for index, (name, _) in enumerate(steps)
            if name == FIXTURE_REVIEW_STEP_NAME
        ),
        None,
    )

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
    awaiting_approval = bool(ctx.get("awaiting_approval"))
    approval_credentials_missing = bool(ctx.get("approval_credentials_missing"))
    if awaiting_approval:
        next_step = None
    if approval_credentials_missing:
        next_step = None
    paused = ctx.get("paused", False)

    step_names = [s[0] for s in steps]
    approval_credentials_ready = credentials_ready
    credentials_blocking = approval_credentials_missing or (
        awaiting_approval and not approval_credentials_ready
    )
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
        elif (
            credentials_blocking
            and ctx.get("started")
            and index == step_count
            and not done
        ):
            status = "missing-credentials"
            icon = "🔐"
            label = _("Credentials required")
        elif (
            awaiting_approval
            and approval_credentials_ready
            and ctx.get("started")
            and index == step_count
            and not done
        ):
            status = "awaiting-approval"
            icon = "🤝"
            label = _("Awaiting approval")
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
    release_manager_owner = manager.owner_display() if manager else ""
    release_manager_admin_url = None
    if manager:
        try:
            release_manager_admin_url = reverse(
                "admin:release_releasemanager_change", args=[manager.pk]
            )
        except NoReverseMatch:
            pass
    pypi_credentials_missing = not manager or manager.to_credentials() is None
    github_credentials_missing = not manager or manager.to_git_credentials() is None

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
        "step_states": step_states,
        "awaiting_approval": awaiting_approval,
        "approval_credentials_missing": approval_credentials_missing,
        "approval_credentials_ready": approval_credentials_ready,
        "release_manager_owner": release_manager_owner,
        "has_release_manager": bool(manager),
        "release_manager_admin_url": release_manager_admin_url,
        "pypi_credentials_missing": pypi_credentials_missing,
        "github_credentials_missing": github_credentials_missing,
        "is_running": is_running,
        "resume_available": resume_available,
        "can_resume": can_resume,
        "dry_run": dry_run_active,
        "dry_run_toggle_enabled": dry_run_toggle_enabled,
        "warnings": ctx.get("warnings", []),
    }
    request.session[session_key] = ctx
    if done or ctx.get("error"):
        if lock_path.exists():
            lock_path.unlink()
    else:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(ctx), encoding="utf-8")
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

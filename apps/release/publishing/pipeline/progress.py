"""Release publishing progress-screen state and guidance helpers."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode, urlparse

from django.http import HttpResponse, JsonResponse
from django.template.loader import get_template
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from apps.core.views.reports.common import DIRTY_COMMIT_DEFAULT_MESSAGE
from apps.core.views.reports.report_rendering import (
    _ensure_template_name,
    _sanitize_release_error_message,
)
from apps.release.models import PackageRelease

from ..workflow import ReleasePublishContext, ReleasePublishWorkflow


def _normalize_release_summary(summary: str) -> str:
    return " ".join((summary or "").strip().split())


def _default_release_body(release: PackageRelease) -> str:
    package_name = release.package.name
    version = release.version
    return (
        f"{package_name} {version} packages the current suite code, release "
        "metadata, and distribution artifacts into a versioned build that "
        "operators can install or upgrade to through the standard Arthexis "
        "release channels."
    )


def _github_release_body(release: PackageRelease) -> str:
    summary = _normalize_release_summary(release.release_summary)
    if summary:
        return summary
    return _default_release_body(release)


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
        return "complete", "\u2705", _("Completed")
    if error and index == step_count:
        return "error", "\u274c", _("Failed")
    if paused and started and index == step_count and not done:
        return "paused", "\u23f8\ufe0f", _("Paused")
    if started and index == step_count and not done:
        return "active", "\u23f3", _("In progress")
    return "pending", "\u2b1c", _("Pending")


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
        "release_default_description": _default_release_body(release),
        "release_description": _github_release_body(release),
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

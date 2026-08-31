"""Release publish workflow orchestration helpers for HTTP views."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from django.contrib import messages
from django.shortcuts import redirect
from django.utils.translation import gettext as _

from apps.repos.models import GitHubToken

from apps.core.views.reports.common import DIRTY_COMMIT_DEFAULT_MESSAGE
from .context import (
    ReleaseContextState,
    load_release_context,
    persist_release_context,
    store_release_context,
)
from .steps import StepDefinition, run_release_step


def _is_pull_request_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc.lower() != "github.com":
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return len(parts) == 4 and parts[2] == "pull" and parts[3].isdigit()


@dataclass(slots=True)
class ReleasePublishContext:
    """Typed context for release publish request/workflow state."""

    step: int = 0
    started: bool = False
    paused: bool = False
    dry_run: bool = False
    error: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> ReleasePublishContext:
        payload = dict(payload or {})
        return cls(
            step=int(payload.pop("step", 0) or 0),
            started=bool(payload.pop("started", False)),
            paused=bool(payload.pop("paused", False)),
            dry_run=bool(payload.pop("dry_run", False)),
            error=payload.pop("error", None),
            extras=payload,
        )

    def to_dict(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            "step": self.step,
            "started": self.started,
            "paused": self.paused,
            "dry_run": self.dry_run,
        }
        if self.error is not None:
            state["error"] = self.error
        for key, value in self.extras.items():
            if key not in state:
                state[key] = value
        return state


class ReleasePublishWorkflow:
    """Own state loading, transitions, and step advancement for release publish."""

    def __init__(
        self,
        *,
        request,
        session_key: str,
        lock_path: Path,
        restart_path: Path,
        clean_redirect_path: Callable,
        collect_dirty_files: Callable,
        validate_manual_git_push: Callable,
        append_log: Callable,
    ) -> None:
        self.request = request
        self.session_key = session_key
        self.lock_path = lock_path
        self.restart_path = restart_path
        self.clean_redirect_path = clean_redirect_path
        self.collect_dirty_files = collect_dirty_files
        self.validate_manual_git_push = validate_manual_git_push
        self.append_log = append_log

    def load(
        self, log_dir_warning_message: str | None
    ) -> tuple[ReleasePublishContext, str | None]:
        session_ctx = self.request.session.get(self.session_key)
        loaded_ctx = load_release_context(session_ctx, self.lock_path)
        typed_ctx = ReleasePublishContext.from_dict(
            ReleaseContextState.from_dict(loaded_ctx).to_dict()
        )
        if not loaded_ctx:
            typed_ctx.step = 0
            if self.restart_path.exists():
                self.restart_path.unlink()

        if log_dir_warning_message:
            typed_ctx.extras["log_dir_warning_message"] = log_dir_warning_message
        else:
            log_dir_warning_message = typed_ctx.extras.get("log_dir_warning_message")

        return typed_ctx, log_dir_warning_message

    def start(
        self, ctx: ReleasePublishContext, *, start_enabled: bool
    ) -> ReleasePublishContext:
        state = ctx.to_dict()
        if self.request.GET.get("set_dry_run") is not None:
            if start_enabled:
                state["dry_run"] = bool(self.request.GET.get("dry_run"))
                self._store(state)
            return ReleasePublishContext.from_dict(state)

        if self.request.GET.get("start"):
            if start_enabled:
                state["dry_run"] = bool(self.request.GET.get("dry_run"))
            state["started"] = True
            state["paused"] = False
        return ReleasePublishContext.from_dict(state)

    def resume(
        self, ctx: ReleasePublishContext
    ) -> tuple[ReleasePublishContext, bool, Any | None]:
        state = ctx.to_dict()
        state["dry_run"] = bool(state.get("dry_run"))

        if self.request.method == "POST" and self.request.POST.get("set_github_token"):
            return self._resume_with_github_token(state)

        if self.request.method == "POST" and self.request.POST.get(
            "set_test_pruning_evidence"
        ):
            return self._resume_with_test_pruning_evidence(state)

        if self.request.method == "POST" and self.request.POST.get("ack_error"):
            return self._resume_ack_error(state)

        resume_requested = bool(self.request.GET.get("resume"))

        if self.request.GET.get("pause") and state.get("started"):
            state["paused"] = True

        if resume_requested:
            if not state.get("started"):
                state["started"] = True
            if state.get("paused"):
                state["paused"] = False

        return ReleasePublishContext.from_dict(state), resume_requested, None

    def poll(self, ctx: ReleasePublishContext) -> tuple[bool, bool]:
        poll_requested = self.request.GET.get("poll") == "1"
        return poll_requested, bool(
            poll_requested and ctx.extras.get("publish_pending")
        )

    def advance(
        self,
        *,
        steps,
        ctx: ReleasePublishContext,
        step_param: str | None,
        release,
        log_path: Path,
        allow_when_paused: bool,
    ) -> tuple[ReleasePublishContext, int]:
        state = ctx.to_dict()
        result = run_release_step(
            steps=[StepDefinition(name=name, handler=func) for name, func in steps],
            ctx=state,
            step_param=step_param,
            step_count=ctx.step,
            release=release,
            log_path=log_path,
            user=self.request.user,
            append_log=self.append_log,
            persist_context=self._persist,
            allow_when_paused=allow_when_paused,
        )
        return ReleasePublishContext.from_dict(result.ctx), result.step_count

    def persist_state(self, ctx: ReleasePublishContext, *, done: bool) -> None:
        state = ctx.to_dict()
        if done or state.get("error"):
            self._store(state)
            if self.lock_path.exists():
                self.lock_path.unlink()
            return
        self._persist(state)

    def template_state(self, ctx: ReleasePublishContext) -> dict[str, Any]:
        """Final adapter for legacy template context key compatibility."""

        return ctx.to_dict()

    def step_progress(
        self, ctx: ReleasePublishContext, *, resume_requested: bool
    ) -> tuple[int, str | None]:
        restart_count = 0
        if self.restart_path.exists():
            try:
                restart_count = int(self.restart_path.read_text(encoding="utf-8"))
            except Exception:
                restart_count = 0
        step_param = self.request.GET.get("step")
        if resume_requested and step_param is None:
            step_param = str(ctx.step)
        return restart_count, step_param

    def _resume_with_github_token(self, state: dict[str, Any]):
        token = (self.request.POST.get("github_token") or "").strip()
        if token:
            store_token = bool(self.request.POST.get("store_github_token"))
            state["github_token"] = token
            state.pop("github_token_required", None)
            if (
                state.get("paused")
                and not state.get("dirty_files")
                and not state.get("pending_git_push")
            ):
                state["paused"] = False
            if store_token and self.request.user.is_authenticated:
                GitHubToken.objects.update_or_create(
                    defaults={"token": token},
                    group=None,
                    user=self.request.user,
                )
                message = _(
                    "GitHub token stored for this publish session and your account."
                )
            else:
                message = _("GitHub token stored for this publish session.")
            messages.success(self.request, message)
            self._persist(state)
        else:
            state.pop("github_token", None)
            messages.error(self.request, _("Enter a GitHub token to continue."))
            self._store(state)

        target = self.clean_redirect_path(self.request, self.request.path)
        return ReleasePublishContext.from_dict(state), False, redirect(target)

    def _resume_with_test_pruning_evidence(self, state: dict[str, Any]):
        pr_url = (self.request.POST.get("test_pruning_pr_url") or "").strip()
        if pr_url and _is_pull_request_url(pr_url):
            state["test_pruning_pr_url"] = pr_url
            state["test_pruning_result"] = {
                "success": True,
                "source": "operator",
                "pr_url": pr_url,
            }
            state.pop("test_pruning_required", None)
            state.pop("test_pruning_error", None)
            state.pop("error", None)
            if not state.get("started"):
                state["started"] = True
            if not state.get("dirty_files") and not state.get("pending_git_push"):
                state["paused"] = False
            messages.success(self.request, _("Test pruning evidence recorded."))
            self._persist(state)
            query = urlencode({"resume": "1", "step": state.get("step", 0)})
            target = (
                f"{self.clean_redirect_path(self.request, self.request.path)}?{query}"
            )
            return ReleasePublishContext.from_dict(state), False, redirect(target)

        state["test_pruning_required"] = True
        if pr_url:
            message = _("Enter a valid GitHub pull request URL to continue.")
        else:
            message = _("Enter the test pruning PR URL to continue.")
        state["test_pruning_error"] = message
        messages.error(self.request, message)
        self._store(state)
        target = self.clean_redirect_path(self.request, self.request.path)
        return ReleasePublishContext.from_dict(state), False, redirect(target)

    def _resume_ack_error(self, state: dict[str, Any]):
        state.pop("error", None)
        dirty_entries = self.collect_dirty_files()
        if dirty_entries:
            state["dirty_files"] = dirty_entries
            state.setdefault("dirty_commit_message", DIRTY_COMMIT_DEFAULT_MESSAGE)
        else:
            state.pop("dirty_files", None)
            state.pop("dirty_log_message", None)
            state.pop("dirty_commit_error", None)

        pending_push = state.get("pending_git_push")
        if pending_push:
            if self.validate_manual_git_push(pending_push):
                state.pop("pending_git_push", None)
                state.pop("pending_git_push_error", None)
            else:
                state["pending_git_push_error"] = _(
                    "Manual push not detected on origin. Confirm the push completed and try again."
                )

        if not state.get("started"):
            state["started"] = True
        state["paused"] = bool(
            state.get("pending_git_push") or state.get("dirty_files")
        )
        self._store(state)

        target = self.clean_redirect_path(self.request, self.request.path)
        return ReleasePublishContext.from_dict(state), False, redirect(target)

    def _persist(self, ctx: dict[str, Any]) -> None:
        persist_release_context(self.request, self.session_key, ctx, self.lock_path)

    def _store(self, ctx: dict[str, Any]) -> None:
        store_release_context(self.request, self.session_key, ctx)

"""Composable release pipeline execution primitives."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from django.utils.translation import gettext as _

from ..exceptions import DirtyRepository, PublishPending


class PersistContext(Protocol):
    """Persistence callback for checkpointing release context."""

    def __call__(self, ctx: dict) -> None: ...


class ReleaseStep(Protocol):
    """Typed protocol for a pipeline step implementation."""

    def __call__(self, release, ctx: dict, log_path: Path, *, user=None) -> None: ...


@dataclass(frozen=True)
class StepDefinition:
    """Named pipeline step used for deterministic orchestration."""

    name: str
    handler: ReleaseStep


@dataclass
class StepRunResult:
    """Result of trying to execute a single step index."""

    ctx: dict
    step_count: int


def run_release_step(
    *,
    steps: list[StepDefinition],
    ctx: dict,
    step_param: str | None,
    step_count: int,
    release,
    log_path: Path,
    user,
    append_log: Callable[[Path, str], None],
    persist_context: PersistContext,
    allow_when_paused: bool = False,
) -> StepRunResult:
    """Execute one pipeline step if the requested step index matches.

    Prerequisites:
    * ``ctx['started']`` must be truthy.
    * ``step_param`` must contain an integer index.

    Side effects:
    * Mutates ``ctx`` with updated step counters and error state.
    * Persists context through ``persist_context`` when state changes.

    Rollback expectations:
    * Individual steps are responsible for rollback/compensation.
    * The orchestrator only records failure and stops progression.
    """

    error = ctx.get("error")
    was_paused = bool(ctx.get("paused"))

    if not ctx.get("started"):
        return StepRunResult(ctx=ctx, step_count=step_count)
    if ctx.get("paused") and not allow_when_paused:
        return StepRunResult(ctx=ctx, step_count=step_count)
    if step_param is None or error or step_count >= len(steps):
        return StepRunResult(ctx=ctx, step_count=step_count)

    try:
        to_run = int(step_param)
    except (TypeError, ValueError):
        ctx["error"] = _("An internal error occurred while running this step.")
        append_log(log_path, "Invalid step parameter; aborting publish step.")
        persist_context(ctx)
        return StepRunResult(ctx=ctx, step_count=step_count)

    if to_run != step_count:
        return StepRunResult(ctx=ctx, step_count=step_count)

    step = steps[to_run]
    try:
        step.handler(release, ctx, log_path, user=user)
    except (DirtyRepository, PublishPending):
        return StepRunResult(ctx=ctx, step_count=step_count)
    except Exception as exc:  # pragma: no cover - defensive logging
        append_log(log_path, f"{step.name} failed: {exc}")
        ctx["error"] = _("An internal error occurred while running this step.")
        ctx.pop("publish_pending", None)
        persist_context(ctx)
        return StepRunResult(ctx=ctx, step_count=step_count)

    step_count += 1
    ctx["step"] = step_count
    if allow_when_paused and was_paused and not ctx.get("publish_pending"):
        ctx["paused"] = False
    if not ctx.get("publish_pending"):
        ctx.pop("publish_pending", None)
    persist_context(ctx)
    return StepRunResult(ctx=ctx, step_count=step_count)

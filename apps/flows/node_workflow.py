"""Node-local workflow helpers powered by lock files."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from django.conf import settings

logger = logging.getLogger(__name__)
DEFAULT_LOCK_DIR = Path(settings.BASE_DIR) / ".locks"


class NodeWorkflowError(Exception):
    """Base exception for node workflows."""


@dataclass
class NodeWorkflowStep:
    """A single executable step in a node workflow."""

    name: str
    func: Callable[..., Any]
    state_getter: str | None = None

    @classmethod
    def from_callable(
        cls, func: Callable[..., Any], *, name: str | None = None, state_getter: str | None = None
    ) -> "NodeWorkflowStep":
        """Create a step from a callable, honoring decorator metadata if present."""

        derived_name = name or getattr(func, "__node_workflow_step__", None) or func.__name__
        derived_state_getter = state_getter or getattr(func, "__node_workflow_state_getter__", None)
        return cls(derived_name, func, derived_state_getter)

    def derive_state(self, result: Any) -> dict[str, Any] | None:
        """Return a state mapping for this step based on the result or metadata."""

        if isinstance(result, Mapping):
            return dict(result)

        getter = self.state_getter
        if not getter:
            return None

        target = result if result is not None else self.func
        if hasattr(target, getter):
            value = getattr(target, getter)
            return value() if callable(value) else value
        return None


class NodeWorkflowStepMixin:
    """Convenience mixin for exposing methods as workflow steps."""

    workflow_state_getter: str | None = "workflow_state"

    def as_workflow_step(self, method_name: str, *, name: str | None = None) -> NodeWorkflowStep:
        """Return a :class:`NodeWorkflowStep` from an instance method."""

        func = getattr(self, method_name)
        return NodeWorkflowStep.from_callable(
            func,
            name=name,
            state_getter=getattr(func, "__node_workflow_state_getter__", None) or self.workflow_state_getter,
        )


def node_workflow_step(
    workflow: str, *, name: str | None = None, state_getter: str | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate a callable as belonging to a named workflow step.

    The decorator is metadata-only and does not alter the callable. The
    metadata can be read with :meth:`NodeWorkflowStep.from_callable` to
    build a workflow without hard-coding step names. ``state_getter``
    defines an attribute or method to call on the callable's return value
    (or the callable itself if ``None`` is returned) to fetch the state to
    persist after running the step.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(func, "__node_workflow_name__", workflow)
        setattr(func, "__node_workflow_step__", name or getattr(func, "__name__", str(func)))
        if state_getter:
            setattr(func, "__node_workflow_state_getter__", state_getter)
        return func

    return decorator


class NodeWorkflow:
    """Execute lock-based workflows on the current node."""

    def __init__(
        self,
        name: str,
        steps: Iterable[NodeWorkflowStep],
        *,
        lock_dir: Path | None = None,
        state_key: str = "step",
    ) -> None:
        self.name = name
        self.steps = list(steps)
        self.lock_dir = lock_dir or DEFAULT_LOCK_DIR
        self.state_key = state_key

    def lock_path(self, identifier: str) -> Path:
        """Return the path used to persist state for this workflow instance."""

        return self.lock_dir / f"{self.name}_{identifier}.json"

    def load_state(self, identifier: str) -> dict[str, Any] | None:
        """Read any persisted state for the workflow instance."""

        lock_path = self.lock_path(identifier)
        if not lock_path.exists():
            return None
        try:
            return json.loads(lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Lock for %s is unreadable; starting fresh", identifier)
            return None

    def _persist_state(self, lock_path: Path, ctx: Mapping[str, Any], *, final: bool = False) -> None:
        if final:
            if lock_path.exists():
                lock_path.unlink()
            return

        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(dict(ctx)), encoding="utf-8")

    def _record_transition(
        self, identifier: str, from_state: Any, to_state: Any, step_name: str
    ) -> None:
        try:
            from apps.flows.models import Transition
        except Exception:  # pragma: no cover - registry or import issues
            logger.exception("Unable to record transition for workflow %s", self.name)
            return

        Transition.objects.create(
            workflow=self.name,
            identifier=identifier,
            from_state="" if from_state is None else str(from_state),
            to_state="" if to_state is None else str(to_state),
            step_name=step_name or "",
        )

    def run(
        self,
        identifier: str,
        *,
        context: dict[str, Any] | None = None,
        run_step: Callable[[NodeWorkflowStep, dict[str, Any]], Any],
        resume: bool = False,
    ) -> dict[str, Any]:
        """Execute the workflow, persisting state after each step.

        ``run_step`` receives the :class:`NodeWorkflowStep` and the
        current mutable context; it may return a mapping to merge into the
        persisted state. If ``resume`` is True, any existing state file is
        read first and merged with ``context`` before execution.
        """

        ctx: dict[str, Any] = {}
        if resume:
            persisted = self.load_state(identifier)
            if persisted:
                ctx.update(persisted)
        if context:
            ctx.update(context)
        ctx.setdefault(self.state_key, 0)

        lock_path = self.lock_path(identifier)
        self._persist_state(lock_path, ctx)

        for index, step in enumerate(self.steps[ctx[self.state_key] :], start=ctx[self.state_key]):
            from_state = ctx[self.state_key]
            try:
                result = run_step(step, ctx)
            except Exception:
                ctx.setdefault("error", "")
                self._persist_state(lock_path, ctx, final=True)
                raise

            state_update = step.derive_state(result)
            if state_update:
                ctx.update(state_update)

            ctx[self.state_key] = index + 1
            self._record_transition(
                identifier,
                from_state,
                ctx[self.state_key],
                step.name,
            )
            self._persist_state(lock_path, ctx)

        self._persist_state(lock_path, ctx, final=True)
        return ctx

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from django.conf import settings

OPERATOR_LOCAL_FEEDBACK_LOCKFILE = "operator-local-feedback.jsonl"
OPERATOR_LONG_WAIT_THRESHOLD_SECONDS = 60.0
OPERATOR_LONG_WAIT_CHECK_SECONDS = 30.0


def operator_lock_dir(base_dir: Path | str | None = None) -> Path:
    return Path(base_dir or settings.BASE_DIR) / ".locks"


def operator_local_feedback_lock_path(base_dir: Path | str | None = None) -> Path:
    return operator_lock_dir(base_dir) / OPERATOR_LOCAL_FEEDBACK_LOCKFILE


def _json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _clean_text(value: object, *, limit: int = 1000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def build_operator_local_feedback_event(story: object) -> dict[str, Any]:
    user = getattr(story, "user", None)
    return {
        "source": "user_story_local_feedback",
        "user_story_id": getattr(story, "pk", None),
        "submitted_at": _json_safe(getattr(story, "submitted_at", None)),
        "path": str(getattr(story, "path", "") or ""),
        "rating": getattr(story, "rating", None),
        "summary": _clean_text(getattr(story, "comments", "")),
        "comments": _clean_text(getattr(story, "comments", "")),
        "messages": _clean_text(getattr(story, "messages", "")),
        "user_id": getattr(story, "user_id", None),
        "username": getattr(user, "username", "") or "",
        "is_superuser": bool(getattr(user, "is_superuser", False)),
        "feedback_tags": list(getattr(story, "feedback_tags", []) or []),
        "issue_destination": str(getattr(story, "issue_destination", "") or ""),
    }


def append_operator_interrupt_event(
    event: Mapping[str, Any],
    *,
    base_dir: Path | str | None = None,
) -> Path:
    path = operator_local_feedback_lock_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(event), sort_keys=True, default=_json_safe)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return path


def append_operator_local_feedback(
    story: object,
    *,
    base_dir: Path | str | None = None,
) -> Path | None:
    user = getattr(story, "user", None)
    if not bool(getattr(user, "is_superuser", False)):
        return None
    return append_operator_interrupt_event(
        build_operator_local_feedback_event(story),
        base_dir=base_dir,
    )


def drain_operator_interrupts(
    *,
    base_dir: Path | str | None = None,
) -> dict[str, Any]:
    path = operator_local_feedback_lock_path(base_dir)
    if not path.exists():
        return {"entries": [], "warnings": [], "path": str(path), "drained": False}

    drain_path = path.with_name(
        f".{path.name}.drain.{os.getpid()}.{time.monotonic_ns()}"
    )
    try:
        path.replace(drain_path)
    except FileNotFoundError:
        return {"entries": [], "warnings": [], "path": str(path), "drained": False}

    entries: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(
            drain_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                warnings.append(
                    {
                        "line": line_number,
                        "error": str(exc),
                        "content": line,
                    }
                )
                continue
            if isinstance(payload, dict):
                entries.append(payload)
            else:
                warnings.append(
                    {
                        "line": line_number,
                        "error": "JSONL entry was not an object",
                        "content": line,
                    }
                )
    finally:
        drain_path.unlink(missing_ok=True)

    return {
        "entries": entries,
        "warnings": warnings,
        "path": str(path),
        "drained": True,
    }


def collect_manual_task_interrupts(
    *,
    base_dir: Path | str | None = None,
    limit: int = 5,
    now=None,
    mark_seen: bool = True,
) -> list[dict[str, Any]]:
    del base_dir, limit, now, mark_seen
    return []


def _completed_wait_result(
    *,
    timeout_seconds: float,
    started_at: float,
    monotonic: Callable[[], float],
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "status": "completed",
        "interrupted": False,
        "timeout_seconds": timeout_seconds,
        "elapsed_seconds": max(monotonic() - started_at, 0.0),
        "context": dict(context or {}),
    }


def _interrupted_wait_result(
    *,
    timeout_seconds: float,
    started_at: float,
    monotonic: Callable[[], float],
    context: Mapping[str, Any] | None,
    drained: Mapping[str, Any],
    manual_tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    feedback_entries = list(drained.get("entries", []) or [])
    warnings = list(drained.get("warnings", []) or [])
    status = (
        "interrupted_by_feedback"
        if feedback_entries or warnings
        else "interrupted_by_manual_task"
    )
    return {
        "status": status,
        "interrupted": True,
        "timeout_seconds": timeout_seconds,
        "elapsed_seconds": max(monotonic() - started_at, 0.0),
        "context": dict(context or {}),
        "feedback": feedback_entries,
        "warnings": warnings,
        "manual_tasks": manual_tasks,
    }


def operator_interruptible_sleep(
    timeout_seconds: float,
    *,
    context: Mapping[str, Any] | None = None,
    base_dir: Path | str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    drain: Callable[[], Mapping[str, Any]] | None = None,
    manual_task_collector: Callable[[], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    timeout = max(float(timeout_seconds), 0.0)
    started_at = monotonic()
    if timeout < OPERATOR_LONG_WAIT_THRESHOLD_SECONDS:
        if timeout:
            sleep(timeout)
        return _completed_wait_result(
            timeout_seconds=timeout,
            started_at=started_at,
            monotonic=monotonic,
            context=context,
        )

    drain_func = drain or (lambda: drain_operator_interrupts(base_dir=base_dir))
    manual_func = manual_task_collector or (
        lambda: collect_manual_task_interrupts(base_dir=base_dir)
    )
    waited = 0.0
    while waited < timeout:
        next_wait = min(OPERATOR_LONG_WAIT_CHECK_SECONDS, timeout - waited)
        if next_wait:
            sleep(next_wait)
            waited += next_wait

        drained = drain_func()
        manual_tasks = manual_func()
        if drained.get("entries") or drained.get("warnings") or manual_tasks:
            return _interrupted_wait_result(
                timeout_seconds=timeout,
                started_at=started_at,
                monotonic=monotonic,
                context=context,
                drained=drained,
                manual_tasks=manual_tasks,
            )

    return _completed_wait_result(
        timeout_seconds=timeout,
        started_at=started_at,
        monotonic=monotonic,
        context=context,
    )


__all__ = [
    "OPERATOR_LOCAL_FEEDBACK_LOCKFILE",
    "append_operator_interrupt_event",
    "append_operator_local_feedback",
    "build_operator_local_feedback_event",
    "collect_manual_task_interrupts",
    "drain_operator_interrupts",
    "operator_interruptible_sleep",
    "operator_local_feedback_lock_path",
]

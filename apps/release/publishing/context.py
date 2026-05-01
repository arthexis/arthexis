"""Session-backed state utilities for release publish workflows.

Responsibilities:
- Persistence and recovery of publish context from session/lockfile.
- Typed dataclass wrappers used by HTTP/pipeline modules.

Allowed dependencies:
- May use stdlib serialization helpers and shared constants.
- Must not call network APIs or execute subprocess commands.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apps.core.views.reports.common import SENSITIVE_CONTEXT_KEYS


@dataclass(slots=True)
class ReleaseContextState:
    """Typed wrapper around mutable release publish context."""

    step: int = 0
    started: bool = False
    paused: bool = False
    dry_run: bool = False
    error: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ReleaseContextState":
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
        data: dict[str, Any] = {
            "step": self.step,
            "started": self.started,
            "paused": self.paused,
            "dry_run": self.dry_run,
        }
        if self.error is not None:
            data["error"] = self.error
        for key, value in self.extras.items():
            if key not in data:
                data[key] = value
        return data


def sanitize_release_context(ctx: dict) -> dict:
    """Return a redacted context map safe for session persistence."""

    return {key: value for key, value in ctx.items() if key not in SENSITIVE_CONTEXT_KEYS}


def store_release_context(request, session_key: str, ctx: dict) -> None:
    """Store the sanitized context in the current Django session."""

    request.session[session_key] = sanitize_release_context(ctx)


def persist_release_context(request, session_key: str, ctx: dict, lock_path: Path) -> None:
    """Persist context to session and lockfile.

    The on-disk lockfile intentionally excludes sensitive values such as
    ``github_token``. Resume flows may require re-entering credentials.
    """

    store_release_context(request, session_key, ctx)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_ctx = sanitize_release_context(ctx)
    lock_path.write_text(json.dumps(lock_ctx), encoding="utf-8")
    lock_path.chmod(0o600)


def load_release_context(session_ctx: dict | None, lock_path: Path) -> dict:
    """Load release context from session and lockfile.

    Sensitive fields are excluded from lockfiles to reduce credential exposure
    on disk, so resume operations rely on session state for those values.
    """

    context = dict(session_ctx) if session_ctx else {}
    if lock_path.exists():
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:
            return context
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key not in context:
                    context[key] = value
    return context

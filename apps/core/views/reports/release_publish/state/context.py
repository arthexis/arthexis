"""Session-backed context helpers for release publish state."""

from __future__ import annotations

import json
from pathlib import Path

from ...common import SENSITIVE_CONTEXT_KEYS


def sanitize_release_context(ctx: dict) -> dict:
    """Return a redacted context map safe for session persistence."""

    return {key: value for key, value in ctx.items() if key not in SENSITIVE_CONTEXT_KEYS}


def store_release_context(request, session_key: str, ctx: dict) -> None:
    """Store the sanitized context in the current Django session."""

    request.session[session_key] = sanitize_release_context(ctx)


def persist_release_context(request, session_key: str, ctx: dict, lock_path: Path) -> None:
    """Persist context to session and lockfile.

    Side effects:
    * Writes sanitized context to session.
    * Writes full context to a lock file for restart recovery.
    """

    store_release_context(request, session_key, ctx)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(ctx), encoding="utf-8")
    lock_path.chmod(0o600)


def load_release_context(session_ctx: dict | None, lock_path: Path) -> dict:
    """Load release context from session and lockfile with token recovery.

    Session state is sanitized before persistence, so sensitive keys (for example,
    ``github_token``) are restored from lockfile when available.
    """

    context = dict(session_ctx) if session_ctx else {}
    if lock_path.exists():
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:
            return context
        if isinstance(payload, dict):
            for key in SENSITIVE_CONTEXT_KEYS:
                if key in payload and key not in context:
                    context[key] = payload[key]
            if not context:
                return payload
    return context

"""Utilities for admin-driven user impersonation sessions."""

from __future__ import annotations

from collections.abc import Mapping

IMPERSONATOR_SESSION_KEY = "core_admin_impersonator_user_id"


def get_impersonator_user_id(session: Mapping[str, object] | None) -> int | None:
    """Return the stored impersonator user id from a session mapping."""

    if session is None:
        return None
    raw = session.get(IMPERSONATOR_SESSION_KEY)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def is_impersonating(session: Mapping[str, object] | None) -> bool:
    """Return whether the current session has an active impersonator id."""

    return get_impersonator_user_id(session) is not None


def store_impersonator_user_id(session, user_id: int) -> None:
    """Persist the original admin user id before impersonation starts."""

    session[IMPERSONATOR_SESSION_KEY] = int(user_id)


def clear_impersonator_user_id(session) -> None:
    """Remove impersonation tracking data from the active session."""

    session.pop(IMPERSONATOR_SESSION_KEY, None)


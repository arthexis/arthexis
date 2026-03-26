"""Allowlisted RFID action hooks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from apps.core.notifications import notify_async

PRE_AUTH_ACTION_ALLOW = "allow"
PRE_AUTH_ACTION_DENY = "deny"
POST_AUTH_ACTION_NOTIFY = "notify"

PRE_AUTH_ACTION_CHOICES = (
    ("", "None"),
    (PRE_AUTH_ACTION_ALLOW, "Allow"),
    (PRE_AUTH_ACTION_DENY, "Deny"),
)
POST_AUTH_ACTION_CHOICES = (
    ("", "None"),
    (POST_AUTH_ACTION_NOTIFY, "Notify"),
)


@dataclass(slots=True)
class RFIDActionContext:
    """Action context prepared from an RFID tag and scan value."""

    tag: Any
    rfid_value: str


@dataclass(slots=True)
class RFIDActionResult:
    """Result payload returned by internal RFID action handlers."""

    allowed: bool = True
    details: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def _action_allow(_ctx: RFIDActionContext) -> RFIDActionResult:
    return RFIDActionResult(allowed=True)


def _action_deny(_ctx: RFIDActionContext) -> RFIDActionResult:
    return RFIDActionResult(allowed=False, error="RFID denied by pre-auth action.")


def _action_notify(ctx: RFIDActionContext) -> RFIDActionResult:
    notify_async(
        f"RFID {getattr(ctx.tag, 'label_id', '')} AUTH",
        f"{ctx.rfid_value} {(getattr(ctx.tag, 'color', '') or '').upper()}".strip(),
    )
    return RFIDActionResult(allowed=True, details={"notified": True})


PRE_AUTH_ACTION_HANDLERS: dict[str, Callable[[RFIDActionContext], RFIDActionResult]] = {
    PRE_AUTH_ACTION_ALLOW: _action_allow,
    PRE_AUTH_ACTION_DENY: _action_deny,
}
POST_AUTH_ACTION_HANDLERS: dict[str, Callable[[RFIDActionContext], RFIDActionResult]] = {
    POST_AUTH_ACTION_NOTIFY: _action_notify,
}


def _dispatch_action(
    action_id: str | None,
    *,
    handlers: dict[str, Callable[[RFIDActionContext], RFIDActionResult]],
    context: RFIDActionContext,
) -> RFIDActionResult:
    normalized = str(action_id or "").strip().lower()
    if not normalized:
        return RFIDActionResult(allowed=True)
    handler = handlers.get(normalized)
    if handler is None:
        return RFIDActionResult(
            allowed=False,
            error=f"Unsupported RFID action: {normalized}",
        )
    return handler(context)


def dispatch_pre_auth_action(
    action_id: str | None,
    *,
    context: RFIDActionContext,
) -> RFIDActionResult:
    """Run an allowlisted pre-auth RFID action."""

    return _dispatch_action(action_id, handlers=PRE_AUTH_ACTION_HANDLERS, context=context)


def dispatch_post_auth_action(
    action_id: str | None,
    *,
    context: RFIDActionContext,
) -> RFIDActionResult:
    """Run an allowlisted post-auth RFID action."""

    return _dispatch_action(action_id, handlers=POST_AUTH_ACTION_HANDLERS, context=context)

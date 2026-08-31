"""Allowlisted RFID action dispatcher used by reader and auth flows."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RFIDActionResult:
    """Result payload returned by an allowlisted RFID action."""

    success: bool = True
    output: str = ""
    error: str = ""


def _normalize_action_id(value: str | None) -> str:
    return str(value or "").strip().upper()


def _action_noop(*, rfid: str, tag, phase: str) -> RFIDActionResult:
    return RFIDActionResult(success=True, output=f"{phase}:noop:{rfid}")


def _action_log(*, rfid: str, tag, phase: str) -> RFIDActionResult:
    logger.info(
        "RFID action %s for label=%s rfid=%s", phase, getattr(tag, "pk", "?"), rfid
    )
    return RFIDActionResult(success=True, output=f"{phase}:logged")


def _action_reject(*, rfid: str, tag, phase: str) -> RFIDActionResult:
    return RFIDActionResult(success=False, error=f"{phase}:rejected")


_ACTIONS: dict[str, Callable[..., RFIDActionResult]] = {
    "LOG": _action_log,
    "NOOP": _action_noop,
    "REJECT": _action_reject,
}


def dispatch_rfid_action(
    *, action_id: str | None, rfid: str, tag, phase: str
) -> RFIDActionResult:
    """Run an allowlisted RFID action and return structured execution details."""

    normalized = _normalize_action_id(action_id)
    if not normalized:
        return RFIDActionResult(success=True)

    handler = _ACTIONS.get(normalized)
    if handler is None:
        return RFIDActionResult(
            success=False, error=f"Unknown RFID action: {normalized}"
        )

    try:
        result = handler(rfid=rfid, tag=tag, phase=phase)
    except Exception as exc:  # pragma: no cover - defensive protection
        logger.warning("RFID action failed: %s", normalized, exc_info=True)
        return RFIDActionResult(success=False, error=str(exc))

    return result


def get_rfid_action_choices() -> list[tuple[str, str]]:
    """Return model/admin choices for allowlisted RFID actions."""

    return [
        ("", "No action"),
        ("LOG", "Log event"),
        ("NOOP", "No-op marker"),
        ("REJECT", "Force reject"),
    ]

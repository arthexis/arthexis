"""Review reply summary formatting and message composition."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def review_reply_summary(
    *,
    commit: str = "",
    changes: Iterable[str] = (),
    validations: Iterable[str] = (),
    notes: Iterable[str] = (),
    feedback_issue: bool = False,
) -> dict[str, Any]:
    """Build a terse review-thread reply body from structured inputs."""

    cleaned_changes = [item.strip() for item in changes if item.strip()]
    cleaned_validations = [item.strip() for item in validations if item.strip()]
    cleaned_notes = [item.strip() for item in notes if item.strip()]
    short_commit = commit.strip()[:12]
    lines = [f"Addressed in {short_commit}." if short_commit else "Addressed."]
    if cleaned_changes and not feedback_issue:
        lines.extend(["", "Changes:"])
        lines.extend(f"- {item}" for item in cleaned_changes)
    if cleaned_validations and not feedback_issue:
        lines.extend(["", "Validation:"])
        lines.extend(f"- {item}" for item in cleaned_validations)
    if cleaned_notes and not feedback_issue:
        lines.extend(["", "Notes:"])
        lines.extend(f"- {item}" for item in cleaned_notes)
    return {
        "commit": short_commit,
        "changes": cleaned_changes,
        "validations": cleaned_validations,
        "notes": cleaned_notes,
        "body": "\n".join(lines).strip() + "\n",
    }

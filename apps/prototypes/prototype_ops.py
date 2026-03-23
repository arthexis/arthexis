"""Compatibility helpers for retired prototype runtime records."""

from __future__ import annotations

from django.utils import timezone

from apps.prototypes.models import Prototype

RETIREMENT_MESSAGE = (
    "Prototype runtime scaffolding has been retired. Records are metadata only."
)


def retire_prototype(prototype: Prototype, *, note: str = "") -> Prototype:
    """Mark a prototype record as retired metadata.

    Parameters:
        prototype: The prototype record to update.
        note: Optional administrative note recorded on the row.

    Returns:
        Prototype: The updated prototype record.
    """

    prototype.is_active = False
    prototype.is_runnable = False
    if prototype.retired_at is None:
        prototype.retired_at = timezone.now()
    if note:
        prototype.retirement_notes = note
    prototype.save(update_fields=["is_active", "is_runnable", "retired_at", "retirement_notes"])
    return prototype

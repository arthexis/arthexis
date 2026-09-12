"""Shared utility package compatibility helpers."""

from __future__ import annotations

import datetime
import enum


if not hasattr(enum, "StrEnum"):

    class StrEnum(str, enum.Enum):
        """Python 3.10-compatible fallback for :class:`enum.StrEnum`."""

        def __str__(self) -> str:
            return str(self.value)

    enum.StrEnum = StrEnum  # type: ignore[attr-defined]

if not hasattr(datetime, "UTC"):
    datetime.UTC = datetime.timezone.utc  # type: ignore[attr-defined]

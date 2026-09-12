"""Shared utility package compatibility helpers."""

from __future__ import annotations

import datetime
import enum
import sys
import typing


if not hasattr(enum, "StrEnum"):

    class StrEnum(str, enum.Enum):
        """Python 3.10-compatible fallback for :class:`enum.StrEnum`."""

        def __str__(self) -> str:
            return str(self.value)

    enum.StrEnum = StrEnum  # type: ignore[attr-defined]
else:
    StrEnum = enum.StrEnum

if not hasattr(datetime, "UTC"):
    datetime.UTC = datetime.timezone.utc  # type: ignore[attr-defined]

if sys.version_info < (3, 11):
    import tomli
    import typing_extensions

    sys.modules.setdefault("tomllib", tomli)
    for _name in (
        "LiteralString",
        "Never",
        "NotRequired",
        "Required",
        "Self",
        "TypeVarTuple",
        "Unpack",
        "assert_never",
        "assert_type",
        "dataclass_transform",
        "reveal_type",
    ):
        if not hasattr(typing, _name):
            setattr(typing, _name, getattr(typing_extensions, _name))

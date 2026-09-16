"""Structured results returned by OCPP simulator operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class BootResult:
    status: str
    current_time: str
    interval: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AuthorizationResult:
    charger: str
    boot: str
    id_tag: str
    authorization: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_dict(self) -> dict[str, Any]:
        """Compatibility alias used by local worker/control responses."""
        return self.to_dict()

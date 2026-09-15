"""Structured results returned by OCPP simulator scenarios."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class BootResult:
    status: str
    current_time: str | None = None
    interval: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AuthorizationResult:
    charger: str
    id_tag: str
    boot: BootResult
    authorization: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

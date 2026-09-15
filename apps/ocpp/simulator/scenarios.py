"""Reusable operations that run against an already-open simulated charger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from .client import OCPP16Simulator
from .results import AuthorizationResult

ResultT = TypeVar("ResultT", covariant=True)


class Scenario(Protocol[ResultT]):
    """A scenario executes operations against an already-open simulator."""

    async def run(self, simulator: OCPP16Simulator) -> ResultT: ...


@dataclass(frozen=True)
class AuthorizeScenario:
    """Authorize one arbitrary RFID/idTag on an already-booted connection."""

    charger: str
    id_tag: str
    boot_status: str = ""

    def __post_init__(self) -> None:
        if not self.id_tag.strip():
            raise ValueError("id_tag cannot be empty")

    async def run(self, simulator: OCPP16Simulator) -> AuthorizationResult:
        status = await simulator.authorize(self.id_tag)
        return AuthorizationResult(
            charger=self.charger,
            boot=self.boot_status,
            id_tag=self.id_tag,
            authorization=status,
        )

"""Reusable operations that run against an already-open simulated charger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .client import OCPP16Simulator
from .results import AuthorizationResult


class Scenario(Protocol):
    """Small scenario boundary retained for later streamed/load scenarios."""

    async def run(self, charger: OCPP16Simulator) -> object: ...


@dataclass(frozen=True)
class AuthorizeScenario:
    """Authorize one arbitrary RFID/idTag on an already-booted connection."""

    charger: str
    id_tag: str
    boot_status: str = ""

    async def run(self, simulator: OCPP16Simulator) -> AuthorizationResult:
        status = await simulator.authorize(self.id_tag)
        return AuthorizationResult(
            charger=self.charger,
            boot=self.boot_status,
            id_tag=self.id_tag,
            authorization=status,
        )

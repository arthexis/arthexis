"""Reusable scenario boundary for OCPP simulator operations."""

from __future__ import annotations

from typing import Protocol, TypeVar

from .client import OCPP16Simulator, SimulatorError
from .results import AuthorizationResult

ResultT = TypeVar("ResultT", covariant=True)


class Scenario(Protocol[ResultT]):
    """A scenario executes operations against an already-created simulator."""

    async def run(self, simulator: OCPP16Simulator) -> ResultT: ...


class AuthorizeScenario:
    """Boot a charge point and ask the remote CSMS to authorize one idTag."""

    def __init__(self, id_tag: str) -> None:
        if not id_tag.strip():
            raise ValueError("id_tag cannot be empty")
        self.id_tag = id_tag

    async def run(self, simulator: OCPP16Simulator) -> AuthorizationResult:
        async with simulator:
            boot = await simulator.boot()
            if boot.status != "Accepted":
                raise SimulatorError(f"BootNotification was not accepted: {boot.status}")
            authorization = await simulator.authorize(self.id_tag)
        return AuthorizationResult(
            charger=simulator.config.charger,
            id_tag=self.id_tag,
            boot=boot,
            authorization=authorization,
        )

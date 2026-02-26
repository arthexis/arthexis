"""Mobility House based EVCS simulator proposal.

This module defines a proposal-ready abstraction for a second-generation EVCS
simulator that uses the `mobilityhouse/ocpp` library instead of manually
constructing OCPP frames.

The implementation is intentionally lightweight and non-invasive:

* it does not alter the existing simulator entry points;
* it provides explicit configuration and dependency validation;
* it exposes an adapter contract that can be wired into the current UI and
  management workflows incrementally.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from typing import Any


class MobilityHouseOcppUnavailableError(ModuleNotFoundError):
    """Raised when the optional `ocpp` package is not installed."""


@dataclass(slots=True)
class MobilityHouseSimulatorConfig:
    """Configuration contract for the proposed Mobility House simulator.

    Attributes:
        charge_point_id: Charge point identifier used in the websocket path.
        central_system_uri: Full websocket URI of the CSMS endpoint.
        heartbeat_interval_s: Heartbeat cadence sent by the charge point.
        meter_interval_s: Meter value cadence sent while charging.
        vendor: Vendor name used in boot notifications.
        model: Model name used in boot notifications.
    """

    charge_point_id: str
    central_system_uri: str
    heartbeat_interval_s: int = 30
    meter_interval_s: int = 10
    vendor: str = "ArthexisSimulator"
    model: str = "EVCS-v2"


@dataclass(slots=True)
class MobilityHouseSimulatorProposal:
    """A proposal object describing the EVCS v2 simulator architecture."""

    config: MobilityHouseSimulatorConfig
    adapter_path: str
    notes: tuple[str, ...]


def ensure_mobilityhouse_ocpp_available() -> None:
    """Validate that `mobilityhouse/ocpp` is importable.

    Raises:
        MobilityHouseOcppUnavailableError: If the `ocpp` package is not
            present in the Python environment.
    """

    if find_spec("ocpp") is None:
        raise MobilityHouseOcppUnavailableError(
            "Install the 'ocpp' package to enable the Mobility House EVCS simulator proposal."
        )


def build_simulator_proposal(
    config: MobilityHouseSimulatorConfig,
) -> MobilityHouseSimulatorProposal:
    """Build a structured EVCS simulator v2 proposal.

    The function verifies dependency availability and returns a static proposal
    payload that downstream UI/admin surfaces can render.
    """

    ensure_mobilityhouse_ocpp_available()
    return MobilityHouseSimulatorProposal(
        config=config,
        adapter_path=(
            f"{MobilityHouseChargePointAdapter.__module__}"
            f".{MobilityHouseChargePointAdapter.__qualname__}"
        ),
        notes=(
            "Use ocpp.v16.ChargePoint or ocpp.v201.ChargePoint as protocol adapters.",
            "Map existing simulator controls (duration, repeat, delay) into asynchronous scenario plugins.",
            "Publish simulator state through the existing apps.ocpp.store logger for continuity.",
            "Gate rollout with a feature flag so legacy JSON-frame simulator remains default.",
        ),
    )


class MobilityHouseChargePointAdapter:
    """Placeholder adapter contract for the proposed EVCS v2 implementation.

    This class is intentionally minimal because the current task is to propose
    the architecture without replacing production simulator flows.
    """

    def __init__(self, proposal: MobilityHouseSimulatorProposal) -> None:
        """Store proposal metadata for future runtime integration."""

        self.proposal = proposal

    async def run(self) -> dict[str, Any]:
        """Return a dry-run payload representing planned runtime behavior."""

        return {
            "charge_point_id": self.proposal.config.charge_point_id,
            "uri": self.proposal.config.central_system_uri,
            "adapter": self.proposal.adapter_path,
            "status": "proposal-only",
        }

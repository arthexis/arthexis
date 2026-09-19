"""Executable retained-OCPP support-matrix reporting."""

from dataclasses import dataclass

from apps.ocpp.models import Charger
from apps.ocpp.protocol.contracts import ActionContract, Direction, ProtocolVersion
from apps.ocpp.protocol.registry import ALL_ACTIONS
from apps.ocpp.protocol.v16.inbound import InboundActions as V16InboundActions
from apps.ocpp.protocol.v16.outbound import VALIDATORS as V16_VALIDATORS
from apps.ocpp.protocol.v201.inbound import InboundActions as V201InboundActions
from apps.ocpp.protocol.v201.outbound import VALIDATORS as V201_VALIDATORS


@dataclass(frozen=True)
class MatrixEntry:
    """One frozen action contract and its executable implementation status."""

    contract: ActionContract
    implemented: bool


def support_matrix() -> tuple[MatrixEntry, ...]:
    """Return every frozen action with its concrete handler/validator status."""
    inbound = _inbound_actions()
    outbound = {
        ProtocolVersion.OCPP_16: set(V16_VALIDATORS),
        ProtocolVersion.OCPP_201: set(V201_VALIDATORS),
    }
    entries = []
    for contract in ALL_ACTIONS:
        actions = (
            inbound[contract.version]
            if contract.direction == Direction.CHARGE_POINT_TO_CSMS
            else outbound[contract.version]
        )
        entries.append(
            MatrixEntry(contract=contract, implemented=contract.action in actions)
        )
    return tuple(entries)


def unimplemented_actions() -> tuple[MatrixEntry, ...]:
    """Return only frozen contracts without an executable implementation."""
    return tuple(entry for entry in support_matrix() if not entry.implemented)


def _inbound_actions() -> dict[ProtocolVersion, set[str]]:
    charger = Charger(identity="support-matrix")
    return {
        ProtocolVersion.OCPP_16: set(V16InboundActions(charger)._handlers),
        ProtocolVersion.OCPP_201: set(V201InboundActions(charger)._handlers),
    }

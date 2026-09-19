"""Lookup for the frozen retained OCPP action matrix."""

from collections.abc import Iterable

from apps.ocpp.protocol.contracts import ActionContract, Direction, ProtocolVersion
from apps.ocpp.protocol.v16.matrix import ACTIONS as V16_ACTIONS
from apps.ocpp.protocol.v201.inbound import ACTIONS as V201_INBOUND_ACTIONS
from apps.ocpp.protocol.v201.outbound import ACTIONS as V201_OUTBOUND_ACTIONS

ALL_ACTIONS = (*V16_ACTIONS, *V201_INBOUND_ACTIONS, *V201_OUTBOUND_ACTIONS)


def _build_registry(
    actions: Iterable[ActionContract],
) -> dict[tuple[ProtocolVersion, Direction, str], ActionContract]:
    registry: dict[tuple[ProtocolVersion, Direction, str], ActionContract] = {}
    for contract in actions:
        key = (contract.version, contract.direction, contract.action)
        if key in registry:
            raise ValueError(f"Duplicate OCPP action contract: {key!r}")
        registry[key] = contract
    return registry


ACTION_REGISTRY = _build_registry(ALL_ACTIONS)


def resolve_action(
    *, version: ProtocolVersion, direction: Direction, action: str
) -> ActionContract | None:
    """Return the contract for one versioned action and message direction."""
    return ACTION_REGISTRY.get((version, direction, action))

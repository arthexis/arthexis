"""Protocol action metadata shared by both retained OCPP versions."""

from dataclasses import dataclass
from enum import StrEnum


class Direction(StrEnum):
    CHARGE_POINT_TO_CSMS = "charge_point_to_csms"
    CSMS_TO_CHARGE_POINT = "csms_to_charge_point"


class ProtocolVersion(StrEnum):
    OCPP_16 = "ocpp1.6"
    OCPP_201 = "ocpp2.0.1"


@dataclass(frozen=True)
class ActionContract:
    """The versioned payload and persistence boundary for one OCPP action."""

    version: ProtocolVersion
    direction: Direction
    action: str
    persistence_owner: str
    request_contract: str
    response_contract: str
    call_error_contract: str = "CallError"


def action_contract(
    *,
    version: ProtocolVersion,
    direction: Direction,
    action: str,
    persistence_owner: str,
) -> ActionContract:
    """Declare one action's protocol payload and call-error contract."""
    return ActionContract(
        version=version,
        direction=direction,
        action=action,
        persistence_owner=persistence_owner,
        request_contract=f"{action}Request",
        response_contract=f"{action}Response",
    )

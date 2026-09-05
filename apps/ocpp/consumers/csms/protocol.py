"""Protocol envelope helpers for CSMS OCPP message handling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NewType, TypeAlias

from ...payload_types import JSONObject, JSONValue

# JSONValue is intentionally present in this module's globals so get_type_hints()
# can resolve JSONObject's recursive alias.


OCPPMessageType: TypeAlias = Literal[2, 3, 4]
OCPPMessageId = NewType("OCPPMessageId", str)
OCPPAction = NewType("OCPPAction", str)
OCPPErrorCode = NewType("OCPPErrorCode", str)


@dataclass(frozen=True)
class OCPPCallEnvelope:
    message_id: OCPPMessageId
    action: OCPPAction
    payload: JSONObject


@dataclass(frozen=True)
class OCPPCallResultEnvelope:
    message_id: OCPPMessageId
    payload: JSONObject


@dataclass(frozen=True)
class OCPPCallErrorEnvelope:
    message_id: OCPPMessageId
    error_code: OCPPErrorCode
    description: str
    details: JSONObject


OCPPEnvelope: TypeAlias = (
    OCPPCallEnvelope | OCPPCallResultEnvelope | OCPPCallErrorEnvelope
)


def message_type(msg: object) -> OCPPMessageType | None:
    """Return a supported OCPP message type discriminator, if present."""

    if not isinstance(msg, list) or not msg:
        return None
    value = msg[0]
    if value == 2:
        return 2
    if value == 3:
        return 3
    if value == 4:
        return 4
    return None


def validate_call_envelope(msg: object) -> OCPPCallEnvelope | None:
    if not isinstance(msg, list) or len(msg) != 4 or msg[0] != 2:
        return None
    message_id = msg[1]
    action = msg[2]
    payload = msg[3]
    if (
        not isinstance(message_id, str)
        or not isinstance(action, str)
        or not isinstance(payload, dict)
    ):
        return None
    return OCPPCallEnvelope(
        message_id=OCPPMessageId(message_id),
        action=OCPPAction(action),
        payload=payload,
    )


def validate_call_result_envelope(msg: object) -> OCPPCallResultEnvelope | None:
    if not isinstance(msg, list) or len(msg) != 3 or msg[0] != 3:
        return None
    message_id = msg[1]
    payload = msg[2]
    if not isinstance(message_id, str) or not isinstance(payload, dict):
        return None
    return OCPPCallResultEnvelope(
        message_id=OCPPMessageId(message_id),
        payload=payload,
    )


def validate_call_error_envelope(msg: object) -> OCPPCallErrorEnvelope | None:
    if not isinstance(msg, list) or len(msg) != 5 or msg[0] != 4:
        return None
    message_id = msg[1]
    error_code = msg[2]
    description = msg[3]
    details = msg[4]
    if (
        not isinstance(message_id, str)
        or not isinstance(error_code, str)
        or not isinstance(description, str)
        or not isinstance(details, dict)
    ):
        return None
    return OCPPCallErrorEnvelope(
        message_id=OCPPMessageId(message_id),
        error_code=OCPPErrorCode(error_code),
        description=description,
        details=details,
    )


def validate_message_envelope(msg: object) -> OCPPEnvelope | None:
    """Validate a supported OCPP frame and return its typed envelope."""

    kind = message_type(msg)
    if kind == 2:
        return validate_call_envelope(msg)
    if kind == 3:
        return validate_call_result_envelope(msg)
    if kind == 4:
        return validate_call_error_envelope(msg)
    return None

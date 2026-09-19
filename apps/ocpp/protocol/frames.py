"""Validation and serialization for OCPP JSON message frames."""

from dataclasses import dataclass
from enum import IntEnum
from typing import TypeAlias

from apps.ocpp.protocol.errors import ProtocolFrameError


class MessageType(IntEnum):
    CALL = 2
    CALL_RESULT = 3
    CALL_ERROR = 4


@dataclass(frozen=True)
class Call:
    unique_id: str
    action: str
    payload: dict[str, object]

    def to_wire(self) -> list[object]:
        return [MessageType.CALL, self.unique_id, self.action, self.payload]


@dataclass(frozen=True)
class CallResult:
    unique_id: str
    payload: dict[str, object]

    def to_wire(self) -> list[object]:
        return [MessageType.CALL_RESULT, self.unique_id, self.payload]


@dataclass(frozen=True)
class CallError:
    unique_id: str
    code: str
    description: str
    details: dict[str, object]

    def to_wire(self) -> list[object]:
        return [
            MessageType.CALL_ERROR,
            self.unique_id,
            self.code,
            self.description,
            self.details,
        ]


Frame: TypeAlias = Call | CallResult | CallError


def parse_frame(content: object) -> Frame:
    """Parse one decoded JSON value into an OCPP frame or raise a safe error."""
    if not isinstance(content, list) or not content:
        raise ProtocolFrameError("FormationViolation", "OCPP frame must be a list.")

    message_type = content[0]
    if message_type == MessageType.CALL:
        return _parse_call(content)
    if message_type == MessageType.CALL_RESULT:
        return _parse_call_result(content)
    if message_type == MessageType.CALL_ERROR:
        return _parse_call_error(content)
    raise ProtocolFrameError("FormationViolation", "Unknown OCPP message type.")


def _parse_call(content: list[object]) -> Call:
    if len(content) != 4:
        raise ProtocolFrameError("FormationViolation", "Invalid OCPP Call frame.")
    unique_id, action, payload = content[1:]
    if not isinstance(unique_id, str) or not isinstance(action, str):
        raise ProtocolFrameError("FormationViolation", "Call identifiers must be text.")
    if not isinstance(payload, dict):
        raise ProtocolFrameError(
            "FormationViolation", "Call payload must be an object."
        )
    return Call(unique_id=unique_id, action=action, payload=payload)


def _parse_call_result(content: list[object]) -> CallResult:
    if len(content) != 3:
        raise ProtocolFrameError("FormationViolation", "Invalid OCPP CallResult frame.")
    unique_id, payload = content[1:]
    if not isinstance(unique_id, str) or not isinstance(payload, dict):
        raise ProtocolFrameError("FormationViolation", "Invalid CallResult payload.")
    return CallResult(unique_id=unique_id, payload=payload)


def _parse_call_error(content: list[object]) -> CallError:
    if len(content) != 5:
        raise ProtocolFrameError("FormationViolation", "Invalid OCPP CallError frame.")
    unique_id, code, description, details = content[1:]
    if not all(isinstance(value, str) for value in (unique_id, code, description)):
        raise ProtocolFrameError("FormationViolation", "Invalid CallError text fields.")
    if not isinstance(details, dict):
        raise ProtocolFrameError(
            "FormationViolation", "CallError details must be an object."
        )
    return CallError(
        unique_id=unique_id,
        code=code,
        description=description,
        details=details,
    )

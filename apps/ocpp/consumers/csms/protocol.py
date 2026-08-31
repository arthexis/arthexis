"""Protocol envelope helpers for CSMS OCPP call handling."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OCPPCallEnvelope:
    message_id: str
    action: str
    payload: dict


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
    return OCPPCallEnvelope(message_id=message_id, action=action, payload=payload)

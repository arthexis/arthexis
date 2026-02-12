"""Message parsing helpers for OCPP websocket frames.

This module intentionally keeps parsing logic independent from websocket lifecycle
code so it can be unit-tested in isolation. It supports the shared envelope
format used by both OCPP 1.6 and OCPP 2.x sessions:

* plain OCPP arrays (``[MessageTypeId, ...]``), and
* forwarded envelopes (``{"ocpp": [...], "meta": {...}}``).

Public extension point:
    ``parse_ocpp_message`` can be reused by alternate dispatch implementations
    that need identical envelope compatibility without subclassing consumers.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ParsedOcppMessage:
    """Normalized parse output for inbound websocket payloads."""

    ocpp_message: list[Any]
    forwarding_meta: dict[str, Any] | None = None


def normalize_raw_message(text_data: str | None, bytes_data: bytes | None) -> str | None:
    """Normalize incoming text/bytes websocket payload into a raw string."""

    if text_data is not None:
        return text_data
    if bytes_data is None:
        return None
    return base64.b64encode(bytes_data).decode("ascii")


def parse_ocpp_message(raw: str) -> ParsedOcppMessage | None:
    """Parse a raw websocket payload into an OCPP frame and optional metadata."""

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if isinstance(parsed, dict):
        ocpp_payload = parsed.get("ocpp")
        if isinstance(ocpp_payload, list) and ocpp_payload:
            meta = parsed.get("meta")
            return ParsedOcppMessage(
                ocpp_message=ocpp_payload,
                forwarding_meta=meta if isinstance(meta, dict) else None,
            )
        return None

    if not isinstance(parsed, list) or not parsed:
        return None
    if parsed[0] == 2 and len(parsed) < 3:
        return None

    return ParsedOcppMessage(ocpp_message=parsed)

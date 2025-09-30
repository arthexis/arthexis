"""Utilities for working with OCPP message exchanges."""

from __future__ import annotations

import json
from typing import Awaitable, Callable, Optional

JsonFrame = list[object]


async def wait_for_call_result(
    recv: Callable[[], Awaitable[str]],
    expected_id: str,
    *,
    handle_call: Optional[Callable[[JsonFrame], Awaitable[bool]]] = None,
) -> dict[str, object]:
    """Return the payload for a specific CALLRESULT frame.

    The helper consumes frames from ``recv`` until it encounters an OCPP ``CALLRESULT``
    (message type ``3``) whose ``uniqueId`` matches ``expected_id``.  ``CALL`` frames
    (message type ``2``) are optionally delegated to ``handle_call`` so callers can
    acknowledge remote commands while waiting for the desired response.  Any other
    frames are ignored.

    A ``CALLError`` (message type ``4``) for the expected ``uniqueId`` raises a
    ``RuntimeError`` so callers can surface the failure to higher layers.
    """

    while True:
        raw = await recv()
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, list) or not message:
            continue

        message_type = message[0]
        if message_type == 2:
            if handle_call is not None:
                handled = await handle_call(message)
                if handled:
                    continue
        elif message_type == 3:
            if len(message) > 1 and str(message[1]) == expected_id:
                payload = message[2] if len(message) > 2 and isinstance(message[2], dict) else {}
                return payload
        elif message_type == 4:
            if len(message) > 1 and str(message[1]) == expected_id:
                error_code = message[2] if len(message) > 2 else "UnknownError"
                description = message[3] if len(message) > 3 else ""
                raise RuntimeError(f"Call {expected_id} failed: {error_code} {description}")

        # Ignore all other frames and continue waiting.


__all__ = ["wait_for_call_result"]

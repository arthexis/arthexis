"""Ephemeral OCPP consumer session state structures.

These dataclasses hold in-memory state tied to a single websocket connection.
They intentionally avoid database coupling and can be reused for OCPP 1.6 and
2.x handlers alike.

Public extension point:
    ``ConsumerSessionState`` may be embedded by alternate consumer
    implementations that need shared state bookkeeping without inheriting
    concrete websocket consumer classes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ConsumerSessionState:
    """In-memory state container for an active charge-point connection."""

    connector_value: int | None = None
    store_key: str = ""
    consumption_task: asyncio.Task | None = None
    consumption_message_uuid: str | None = None
    header_reference_created: bool = False
    forwarding_meta: dict[str, Any] | None = field(default=None)

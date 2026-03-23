"""Shared mutable consumer state for OCPP websocket sessions.

The structures in this module are intentionally version-agnostic and are used by
both OCPP 1.6 and OCPP 2.x handlers to track per-connection state that should
not be persisted directly in the database.
"""

from dataclasses import dataclass, field
import asyncio


@dataclass(slots=True)
class ConsumerState:
    """In-memory state container for a single CSMS websocket consumer.

    Assumes an OCPP 1.6/2.x websocket session lifecycle where connector context,
    forwarding metadata, and optional periodic update tasks are tied to the
    connection and reset on disconnect. This structure has no direct DB writes.
    """

    connector_value: int | None = None
    store_key: str = ""
    consumption_task: asyncio.Task | None = None
    consumption_message_uuid: str | None = None
    header_reference_created: bool = False
    forwarding_meta: dict | None = field(default=None)

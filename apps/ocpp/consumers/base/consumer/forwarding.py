"""Forwarding/session refresh helpers for charge point message relays.

The wrapped consumer methods coordinate OCPP message forwarding between nodes
and persist heartbeat/watermark metadata on ``Charger``/``CPForwarder`` rows.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import CSMSConsumer


class ForwardingHandler:
    """Adapter for forwarding operations that involve network and DB side effects."""

    def __init__(self, consumer: "CSMSConsumer") -> None:
        self.consumer = consumer

    async def forward_message(self, action: str, raw: str) -> None:
        """Forward a charge point message to a configured remote CSMS session."""

        await self.consumer._forward_charge_point_message_legacy(action, raw)

    async def forward_reply(self, message_id: str, raw: str) -> None:
        """Forward a reply frame and refresh session forwarding state."""

        await self.consumer._forward_charge_point_reply_legacy(message_id, raw)

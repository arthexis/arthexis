"""Connection/authentication helpers for OCPP websocket consumers.

These helpers cover connect/disconnect orchestration and access checks before a
charge point session is accepted. DB side effects occur in delegated consumer
methods that create/update ``Charger`` records and session metadata.
"""

from typing import TYPE_CHECKING

from ....models import Charger

if TYPE_CHECKING:
    from . import CSMSConsumer


class ConnectionHandler:
    """Adapter for connection lifecycle functions with auth/allow checks."""

    def __init__(self, consumer: "CSMSConsumer") -> None:
        self.consumer = consumer

    async def allow_charge_point_connection(self, existing_charger: Charger | None) -> bool:
        """Evaluate local feature flags and charger existence for admission."""

        return await self.consumer._allow_charge_point_connection_legacy(existing_charger)

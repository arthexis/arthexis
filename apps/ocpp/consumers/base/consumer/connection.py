"""Connection/authentication helpers for OCPP websocket consumers.

These helpers cover connect/disconnect orchestration and access checks before a
charge point session is accepted. DB side effects occur in delegated consumer
methods that create/update ``Charger`` records and session metadata.
"""

from typing import TYPE_CHECKING

from channels.db import database_sync_to_async

from ....models import Charger
from .connection_flow import ConnectionAdmissionService

if TYPE_CHECKING:
    from . import CSMSConsumer


class ConnectionHandler:
    """Adapter for connection lifecycle functions with auth/allow checks."""

    def __init__(self, consumer: "CSMSConsumer") -> None:
        self.consumer = consumer
        self._admission_service = ConnectionAdmissionService(db_call=database_sync_to_async)

    async def allow_charge_point_connection(self, existing_charger: Charger | None) -> bool:
        """Evaluate local feature flags and charger existence for admission."""

        return await self._admission_service.allow_charge_point_connection(
            self.consumer, existing_charger
        )

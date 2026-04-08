"""Availability-related handlers for CP->CSMS processing."""

from __future__ import annotations

from channels.db import database_sync_to_async

from apps.ocpp import store
from apps.ocpp.models import Charger

from apps.ocpp.consumers.csms import persistence


class AvailabilityHandlersMixin:
    """Handle charger availability transitions derived from inbound status flow."""

    async def _handle_available_status_transition(self, connector_value: int | None) -> None:
        """Close cached active session state when a connector becomes available."""

        if connector_value is None:
            return
        tx_obj = store.transactions.pop(self.store_key, None)
        if tx_obj:
            await self._cancel_consumption_message()
            store.end_session_log(self.store_key)
            store.stop_session_lock()

    async def _sync_availability_state_from_status(
        self,
        status: str,
        status_timestamp,
        connector_value: int | None,
    ) -> None:
        """Persist availability metadata inferred from a StatusNotification payload."""

        availability_state = Charger.availability_state_from_status(status)
        if not availability_state:
            return
        await self._update_availability_state(
            availability_state,
            status_timestamp,
            connector_value,
        )

    async def _update_availability_state(
        self,
        state: str,
        timestamp,
        connector_value: int | None,
    ) -> None:
        """Persist availability state for current charger and cached references."""

        targets = await database_sync_to_async(persistence.update_availability_state_records)(
            charger_id=self.charger_id,
            connector_value=connector_value,
            state=state,
            timestamp=timestamp,
        )
        updates = {
            "availability_state": state,
            "availability_state_updated_at": timestamp,
        }
        for target in targets:
            if self.charger and self.charger.pk == target.pk:
                for field, value in updates.items():
                    setattr(self.charger, field, value)
            if self.aggregate_charger and self.aggregate_charger.pk == target.pk:
                for field, value in updates.items():
                    setattr(self.aggregate_charger, field, value)

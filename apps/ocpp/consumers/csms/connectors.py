"""Connector assignment helpers for the CSMS websocket consumer."""

from __future__ import annotations

from collections import deque

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async

from apps.ocpp import store
from apps.ocpp.consumers.base.identity import _register_log_names_for_identity
from apps.ocpp.consumers.path_metadata import bounded_last_path
from apps.ocpp.models import Charger, ChargingStation


class CSMSConnectorAssignmentMixin:
    """Maintain charger connector identity while keeping the consumer entrypoint small."""

    async def _assign_connector(self, connector: int | str | None) -> None:
        """Ensure ``self.charger`` matches the provided connector id."""
        if connector in (None, "", "-"):
            connector_value = None
        else:
            try:
                connector_value = int(connector)
                if connector_value == 0:
                    connector_value = None
            except (TypeError, ValueError):
                return
        if connector_value is None:
            if not getattr(self, "charging_station", None):
                self.charging_station, _ = await database_sync_to_async(
                    ChargingStation.objects.get_or_create
                )(
                    station_id=self.charger_id,
                    defaults={
                        "last_path": bounded_last_path(self.scope, ChargingStation)
                    },
                )
            aggregate = await database_sync_to_async(
                lambda: Charger.objects.filter(
                    charger_id=self.charger_id,
                    connector_id=None,
                ).first()
            )()
            self.aggregate_charger = aggregate
            self.charger = aggregate
            previous_key = self.store_key
            new_key = store.identity_key(self.charger_id, None)
            if previous_key != new_key:
                existing_consumer = store.connections.get(new_key)
                if existing_consumer is not None and existing_consumer is not self:
                    await existing_consumer.close()
                store.reassign_identity(previous_key, new_key)
                store.connections[new_key] = self
                store.logs["charger"].setdefault(
                    new_key, deque(maxlen=store.MAX_IN_MEMORY_LOG_ENTRIES)
                )
            friendly_name = self.charger_id
            _register_log_names_for_identity(self.charger_id, None, friendly_name)
            self.store_key = new_key
            self.connector_value = None
            return
        if (
            self.charger is not None
            and self.connector_value == connector_value
            and self.charger.connector_id == connector_value
        ):
            return
        if not getattr(self, "charging_station", None):
            self.charging_station, _ = await database_sync_to_async(
                ChargingStation.objects.get_or_create
            )(
                station_id=self.charger_id,
                defaults={
                    "last_path": bounded_last_path(self.scope, ChargingStation)
                },
            )
        existing = await database_sync_to_async(
            Charger.objects.filter(
                charger_id=self.charger_id, connector_id=connector_value
            ).first
        )()
        if existing:
            self.charger = existing
            update_fields = []
            if (
                self.charging_station
                and self.charger.charging_station_id != self.charging_station.pk
            ):
                self.charger.charging_station = self.charging_station
                update_fields.append("charging_station")
            path = bounded_last_path(self.scope, Charger)
            if path and self.charger.last_path != path:
                self.charger.last_path = path
                update_fields.append("last_path")
            if update_fields:
                await database_sync_to_async(self.charger.save)(
                    update_fields=update_fields
                )
            await database_sync_to_async(self.charger.refresh_manager_node)()
        else:

            def _create_connector():
                charger, _ = Charger.objects.get_or_create(
                    charger_id=self.charger_id,
                    connector_id=connector_value,
                    defaults={
                        "last_path": bounded_last_path(self.scope, Charger),
                        "charging_station": self.charging_station,
                    },
                )
                if (
                    self.charging_station
                    and charger.charging_station_id != self.charging_station.pk
                ):
                    charger.charging_station = self.charging_station
                    charger.save(update_fields=["charging_station"])
                path = bounded_last_path(self.scope, Charger)
                if path and charger.last_path != path:
                    charger.last_path = path
                    charger.save(update_fields=["last_path"])
                charger.refresh_manager_node()
                return charger

            self.charger = await database_sync_to_async(_create_connector)()
        previous_key = self.store_key
        new_key = store.identity_key(self.charger_id, connector_value)
        if previous_key != new_key:
            existing_consumer = store.connections.get(new_key)
            if existing_consumer is not None and existing_consumer is not self:
                await existing_consumer.close()
            store.reassign_identity(previous_key, new_key)
            store.connections[new_key] = self
            store.logs["charger"].setdefault(
                new_key, deque(maxlen=store.MAX_IN_MEMORY_LOG_ENTRIES)
            )
        connector_name = await sync_to_async(
            lambda: self.charger.name or self.charger.charger_id
        )()
        _register_log_names_for_identity(
            self.charger_id, connector_value, connector_name
        )
        aggregate_name = ""
        if self.aggregate_charger:
            aggregate_name = await sync_to_async(
                lambda: self.aggregate_charger.name or self.aggregate_charger.charger_id
            )()
        _register_log_names_for_identity(
            self.charger_id, None, aggregate_name or self.charger_id
        )
        self.store_key = new_key
        self.connector_value = connector_value

"""Connection flow mixin for OCPP consumer lifecycle handling."""

import asyncio
import logging

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from django.conf import settings

from apps.features.models import Feature
from apps.nodes.models import Node

from ... import store
from apps.forwarder.ocpp import forwarder
from ...forwarder_feature import ocpp_forwarder_enabled
from ...models import Charger, ChargingStation, Transaction
from ..connection import RateLimitedConnectionMixin
from .identity import _register_log_names_for_identity, _resolve_client_ip
from config.offline import requires_network

logger = logging.getLogger(__name__)

CHARGER_CREATION_FEATURE_SLUG = "cp-auto-enrollment"
OCPP_VERSION_FEATURE_SLUGS = {
    "ocpp1.6": "ocpp-16-charge-point",
    "ocpp2.0.1": "ocpp-201-charge-point",
    "ocpp2.1": "ocpp-21-charge-point",
}
CREATION_FEATURE_FALLBACK_SLUGS = [
    CHARGER_CREATION_FEATURE_SLUG,
    *OCPP_VERSION_FEATURE_SLUGS.values(),
]


class ConnectionFlowMixin:
    """Own connect/disconnect orchestration and admission helper methods."""

    async def _ensure_charger_record(self, existing_charger: Charger | None) -> bool:
        """Ensure a charger record exists and refresh cached metadata."""
        station, _station_created = await database_sync_to_async(
            ChargingStation.objects.get_or_create
        )(
            station_id=self.charger_id,
            defaults={"last_path": self.scope.get("path", "")},
        )
        self.charging_station = station
        self.charger = existing_charger
        if existing_charger is not None:
            if self.charger.charging_station_id != station.pk:
                self.charger.charging_station = station
                await database_sync_to_async(self.charger.save)(
                    update_fields=["charging_station"]
                )
            await database_sync_to_async(self.charger.refresh_manager_node)()
            self.aggregate_charger = self.charger
            await self._clear_cached_status_fields()
            return False
        self.charger, charger_created = await database_sync_to_async(
            Charger.objects.get_or_create
        )(
            charger_id=self.charger_id,
            connector_id=None,
            defaults={
                "last_path": self.scope.get("path", ""),
                "charging_station": station,
            },
        )
        if self.charger.charging_station_id != station.pk:
            self.charger.charging_station = station
            await database_sync_to_async(self.charger.save)(
                update_fields=["charging_station"]
            )
        if self.scope.get("path") and self.charger.last_path != self.scope.get("path"):
            self.charger.last_path = self.scope.get("path")
            await database_sync_to_async(self.charger.save)(update_fields=["last_path"])
        self.aggregate_charger = self.charger
        await self._clear_cached_status_fields()
        return charger_created

    async def _register_charger_logs(self) -> None:
        """Register charger log names based on location or charger id."""
        charger = getattr(self, "charger", None)
        location_name = await sync_to_async(
            lambda: charger.location.name if charger and charger.location else ""
        )()
        friendly_name = location_name or self.charger_id
        _register_log_names_for_identity(self.charger_id, None, friendly_name)

    async def _allow_charge_point_connection_legacy(
        self, existing_charger: Charger | None
    ) -> bool:
        """Gate new charge-point websocket admissions using suite feature state."""

        if existing_charger is not None:
            self._charge_point_connection_reason = None
            return True

        def _resolve_feature_reason() -> str | None:
            node = Node.get_local()
            offered_subprotocols = self._get_offered_subprotocols()
            offered_subprotocols_were_explicit = "subprotocols" in self.scope
            requested_version = None
            if offered_subprotocols:
                requested_version = self._canonicalize_ocpp_subprotocol(
                    self._select_subprotocol(offered_subprotocols, None)
                )
            elif not offered_subprotocols_were_explicit:
                requested_version = getattr(self, "ocpp_version", None) or "ocpp1.6"

            requested_feature_slug = OCPP_VERSION_FEATURE_SLUGS.get(
                requested_version,
                CHARGER_CREATION_FEATURE_SLUG,
            )
            if requested_feature_slug == CHARGER_CREATION_FEATURE_SLUG:
                feature_slugs = CREATION_FEATURE_FALLBACK_SLUGS
            else:
                feature_slugs = [requested_feature_slug, CHARGER_CREATION_FEATURE_SLUG]

            features = list(
                Feature.objects.filter(slug__in=CREATION_FEATURE_FALLBACK_SLUGS)
            )
            feature_by_slug = {feature.slug: feature for feature in features}
            ordered_features = [
                feature_by_slug[slug] for slug in feature_slugs if slug in feature_by_slug
            ]
            configured_creation_features = [
                feature_by_slug[slug]
                for slug in CREATION_FEATURE_FALLBACK_SLUGS
                if slug in feature_by_slug
            ]
            if not configured_creation_features:
                logger.info(
                    "Charge point connection allowed without creation feature gates because none of %s are configured.",
                    ", ".join(CREATION_FEATURE_FALLBACK_SLUGS),
                )
                return None

            feature_candidates = ordered_features
            if requested_feature_slug != CHARGER_CREATION_FEATURE_SLUG and not ordered_features:
                logger.info(
                    "Charge point connection blocked: requested creation feature %s is not configured while other charge-point gates remain configured.",
                    requested_feature_slug,
                )
                return "creation-feature-disabled"

            for feature in feature_candidates:
                if feature.is_enabled_for_node(node=node):
                    return None

            enabled_globally = [
                feature.slug for feature in feature_candidates if feature.is_enabled
            ]
            if enabled_globally:
                logger.info(
                    "Charge point connection blocked: creation features %s require an unavailable node capability.",
                    ", ".join(enabled_globally),
                )
                return "creation-node-feature-disabled"
            logger.info(
                "Charge point connection blocked: creation features %s are disabled.",
                ", ".join(feature.slug for feature in feature_candidates),
            )
            return "creation-feature-disabled"

        self._charge_point_connection_reason = await database_sync_to_async(
            _resolve_feature_reason
        )()
        return self._charge_point_connection_reason is None

    async def _allow_charge_point_connection(
        self, existing_charger: Charger | None
    ) -> bool:
        """Compatibility adapter to preserve admission entrypoint."""
        return await self._allow_charge_point_connection_legacy(existing_charger)

    @requires_network
    async def connect(self):
        """Accept and initialize a charge point websocket connection."""
        raw_serial = self._extract_serial_identifier()
        if not await self._validate_serial_or_reject(raw_serial):
            return
        self.connector_value: int | None = None
        self.store_key = store.pending_key(self.charger_id)
        self.aggregate_charger: Charger | None = None
        self._consumption_task: asyncio.Task | None = None
        self._consumption_message_uuid: str | None = None
        self.client_ip = _resolve_client_ip(self.scope)
        self._header_reference_created = False
        existing_charger = await database_sync_to_async(
            lambda: Charger.objects.select_related(
                "ws_auth_user", "ws_auth_group", "station_model", "charging_station"
            )
            .filter(charger_id=self.charger_id, connector_id=None)
            .first(),
            thread_sensitive=False,
        )()
        subprotocol = self._negotiate_ocpp_version(existing_charger)
        if not await self._enforce_ws_auth(existing_charger):
            return
        if not await self._allow_charge_point_connection(existing_charger):
            await self.close()
            return
        if not await self._accept_connection(subprotocol):
            return
        created = await self._ensure_charger_record(existing_charger)
        await self._register_charger_logs()

        restored_calls = store.restore_pending_calls(self.charger_id)
        if restored_calls:
            store.add_log(
                self.store_key,
                f"Restored {len(restored_calls)} pending call(s) after reconnect",
                log_type="charger",
            )

        if await database_sync_to_async(ocpp_forwarder_enabled)(default=True):
            if not created:
                await database_sync_to_async(forwarder.sync_forwarded_charge_points)(
                    refresh_forwarders=False
                )
            forwarder.ensure_keepalive_task(
                idle_seconds=int(getattr(settings, "OCPP_FORWARDER_PING_INTERVAL", 60))
            )
        else:
            await database_sync_to_async(forwarder.clear_sessions)()

    async def disconnect(self, close_code):
        """Release connection resources even when connect rejected early."""
        store.release_ip_connection(getattr(self, "client_ip", None), self)
        charger_id = getattr(self, "charger_id", "")
        connector_value = getattr(self, "connector_value", None)
        store_key = getattr(self, "store_key", store.pending_key(charger_id))
        tx_obj: Transaction | None = None
        if charger_id:
            tx_obj = store.get_transaction(charger_id, connector_value)
        if tx_obj and hasattr(self, "_consumption_task"):
            await self._update_consumption_message(tx_obj.pk)
        if hasattr(self, "_consumption_task"):
            await self._cancel_consumption_message()
        store.connections.pop(store_key, None)
        pending_key = store.pending_key(charger_id)
        if store_key != pending_key:
            store.connections.pop(pending_key, None)
        store.end_session_log(store_key)
        store.stop_session_lock()
        if charger_id:
            store.clear_pending_calls(charger_id)
        store.add_log(store_key, f"Closed (code={close_code})", log_type="charger")

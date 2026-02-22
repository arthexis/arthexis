"""Connection flow mixin for the CSMS consumer."""

import asyncio
import logging

from channels.db import database_sync_to_async
from django.conf import settings
from config.offline import requires_network

from apps.features.models import Feature
from apps.nodes.models import Node, NodeFeature

from .... import store
from ....forwarder import forwarder
from ....models import Charger
from ..identity import _resolve_client_ip

CHARGER_CREATION_FEATURE_SLUG = "standard-charge-point"
CHARGE_POINT_FEATURE_SLUG = "charge-points"

logger = logging.getLogger(__name__)


class ConnectionFlowMixin:
    """Provide connect/disconnect orchestration and admission checks."""

    async def _allow_charge_point_connection(
        self, existing_charger: Charger | None
    ) -> bool:
        """Wrapper that delegates charge-point admission checks to connection helper."""

        return await self._connection_handler().allow_charge_point_connection(
            existing_charger
        )

    async def _allow_charge_point_connection_legacy(
        self, existing_charger: Charger | None
    ) -> bool:
        """Return whether the charge point connection should be accepted."""

        def _resolve_feature_state() -> tuple[bool, str | None]:
            node = Node.get_local()
            if not node:
                logger.warning(
                    "Charge point connection allowed because no local node is registered."
                )
                return True, "node-missing"

            feature = (
                Feature.objects.select_related("node_feature")
                .filter(slug=CHARGER_CREATION_FEATURE_SLUG)
                .first()
            )
            if not feature:
                logger.warning(
                    "Charge point creation feature %s missing; treating as enabled.",
                    CHARGER_CREATION_FEATURE_SLUG,
                )
            node_feature = feature.node_feature if feature else None
            if not node_feature:
                node_feature = NodeFeature.objects.filter(
                    slug=CHARGE_POINT_FEATURE_SLUG
                ).first()

            if not node_feature:
                logger.warning(
                    "Charge point node feature %s missing; treating as enabled.",
                    CHARGE_POINT_FEATURE_SLUG,
                )
            elif not node_feature.is_enabled:
                logger.info(
                    "Charge point connection blocked: node feature %s disabled.",
                    node_feature.slug,
                )
                return False, "node-feature-disabled"

            if feature and not feature.is_enabled:
                if existing_charger:
                    logger.info(
                        "Charge point creation disabled; allowing known charger %s.",
                        existing_charger.charger_id,
                    )
                    return True, "creation-disabled-known"
                logger.info(
                    "Charge point creation disabled; blocking unknown charger %s.",
                    getattr(self, "charger_id", "unknown"),
                )
                return False, "creation-disabled-unknown"

            return True, None

        allowed, _reason = await database_sync_to_async(_resolve_feature_state)()
        return allowed

    @requires_network
    async def connect(self):
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
                "ws_auth_user", "ws_auth_group", "station_model"
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

        if not created:
            await database_sync_to_async(
                forwarder.sync_forwarded_charge_points
            )(refresh_forwarders=False)
        forwarder.ensure_keepalive_task(
            idle_seconds=int(
                getattr(settings, "OCPP_FORWARDER_PING_INTERVAL", 60)
            )
        )

    async def disconnect(self, close_code):
        store.release_ip_connection(getattr(self, "client_ip", None), self)
        charger_id = getattr(self, "charger_id", None)
        connector_value = getattr(self, "connector_value", None)
        store_key = getattr(self, "store_key", None)

        if not charger_id or not store_key:
            return

        tx_obj = None
        if charger_id:
            tx_obj = store.get_transaction(charger_id, connector_value)
        if tx_obj:
            await self._update_consumption_message(tx_obj.pk)
        await self._cancel_consumption_message()
        store.connections.pop(store_key, None)
        pending_key = store.pending_key(charger_id)
        if store_key != pending_key:
            store.connections.pop(pending_key, None)
        store.end_session_log(store_key)
        store.stop_session_lock()
        store.clear_pending_calls(charger_id)
        store.add_log(store_key, f"Closed (code={close_code})", log_type="charger")

"""Status and availability action handlers for CSMS consumer."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from datetime import timezone as dt_timezone

from channels.db import database_sync_to_async
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.ocpp import auto_start, store
from apps.ocpp.consumers.csms import persistence
from apps.ocpp.models import AutoStartAttempt, Charger
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

logger = logging.getLogger(__name__)


class StatusHandlersMixin:
    """Handle heartbeat and status notification actions."""

    async def _configured_auto_start_id_tag(self) -> str:
        """Return the current connector setting, falling back to the station."""

        chargers = (
            getattr(self, "charger", None),
            getattr(self, "aggregate_charger", None),
        )
        charger_pks = [
            charger.pk for charger in chargers if getattr(charger, "pk", None)
        ]
        configured = await database_sync_to_async(
            lambda: dict(
                Charger.objects.filter(pk__in=charger_pks).values_list(
                    "pk", "auto_start_id_tag"
                )
            )
        )()
        for charger in chargers:
            id_tag = str(configured.get(getattr(charger, "pk", None), "") or "").strip()
            if id_tag:
                return id_tag
        return ""

    def _auto_start_reservation_scope(self, evse_id: object = None) -> str:
        """Return the status identity that owns one auto-start reservation."""

        ocpp_version = str(getattr(self, "ocpp_version", "") or "")
        if ocpp_version.startswith(("ocpp2.0", "ocpp2.1")):
            try:
                normalized_evse_id = int(evse_id)
            except (TypeError, ValueError):
                normalized_evse_id = None
            if normalized_evse_id and normalized_evse_id > 0:
                return f"evse:{normalized_evse_id}"
        connector_value = getattr(self, "connector_value", None)
        return (
            f"connector:{connector_value if connector_value is not None else 'station'}"
        )

    def _is_auto_start_status(self, status: str) -> bool:
        """Return whether the protocol reports a newly plugged-in vehicle."""

        normalized_status = (status or "").strip().casefold()
        ocpp_version = str(getattr(self, "ocpp_version", "") or "")
        if ocpp_version.startswith(("ocpp2.0", "ocpp2.1")):
            return normalized_status == "occupied"
        return normalized_status == "preparing"

    def _is_auto_start_connected_status(self, status: str) -> bool:
        """Return whether a legacy connector remains in the same plug-in cycle."""

        normalized_status = (status or "").strip().casefold()
        ocpp_version = str(getattr(self, "ocpp_version", "") or "")
        if ocpp_version.startswith(("ocpp2.0", "ocpp2.1")):
            return normalized_status == "occupied"
        return normalized_status in {
            "preparing",
            "charging",
            "suspendedev",
            "suspendedevse",
            "finishing",
        }

    async def _clear_auto_start_reservation(self, reservation_scope: str) -> None:
        """Allow a new request after the connector leaves its plugged-in state."""

        charger = getattr(self, "charger", None)
        charger_pk = getattr(charger, "pk", None)
        if not charger_pk:
            return
        await database_sync_to_async(auto_start.release_scope)(
            charger_pk=charger_pk,
            reservation_scope=reservation_scope,
        )

    @staticmethod
    async def _release_auto_start_reservation_on_timeout(attempt_id: str) -> None:
        """Timeout only the matching persisted auto-start attempt."""

        await database_sync_to_async(auto_start.transition_attempt)(
            attempt_id,
            state=AutoStartAttempt.State.TIMED_OUT,
            retry=True,
            from_states=(AutoStartAttempt.State.REQUESTED,),
        )

    def _normalized_status_notification_payload(self, payload: object) -> dict:
        """Return legacy-compatible status payload keys for OCPP 2.1 parity."""

        payload_data = payload if isinstance(payload, dict) else {}
        normalized = dict(payload_data)

        connector_id = payload_data.get("connectorId")
        if connector_id is None:
            evse_data = payload_data.get("evse")
            if isinstance(evse_data, dict):
                connector_id = evse_data.get("connectorId")
                if connector_id is None:
                    connector_id = evse_data.get("id")
        if connector_id is None:
            connector_id = payload_data.get("evseId")
        if connector_id is not None:
            normalized["connectorId"] = connector_id

        status_value = payload_data.get("status")
        if status_value is None:
            status_value = payload_data.get("connectorStatus")
        if status_value is not None:
            normalized["status"] = status_value

        if (
            normalized.get("errorCode") is None
            and payload_data.get("connectorStatus") is not None
        ):
            normalized["errorCode"] = "NoError"

        status_info = payload_data.get("statusInfo")
        if isinstance(status_info, dict):
            if not normalized.get("info"):
                normalized["info"] = status_info.get("additionalInfo")
            if not normalized.get("vendorId"):
                normalized["vendorId"] = status_info.get("reasonCode")

        return normalized

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "Heartbeat")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "Heartbeat")
    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "Heartbeat")
    async def _handle_heartbeat_action(self, payload, msg_id, raw, text_data):
        current_time = datetime.now(dt_timezone.utc).isoformat().replace("+00:00", "Z")
        reply_payload = {"currentTime": current_time}
        now = timezone.now()
        self.charger.last_heartbeat = now
        if self.aggregate_charger and self.aggregate_charger is not self.charger:
            self.aggregate_charger.last_heartbeat = now
        await database_sync_to_async(
            Charger.objects.filter(charger_id=self.charger_id).update
        )(last_heartbeat=now)
        return reply_payload

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "StatusNotification")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "StatusNotification")
    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "StatusNotification")
    async def _handle_status_notification_action(self, payload, msg_id, raw, text_data):
        payload_data = self._normalized_status_notification_payload(payload)
        await self._assign_connector(payload_data.get("connectorId"))
        status = (payload_data.get("status") or "").strip()
        error_code = (payload_data.get("errorCode") or "").strip()
        vendor_info = {
            key: value
            for key, value in (
                ("info", payload_data.get("info")),
                ("vendorId", payload_data.get("vendorId")),
            )
            if value
        }
        vendor_value = vendor_info or None
        timestamp_raw = payload_data.get("timestamp")
        status_timestamp = parse_datetime(timestamp_raw) if timestamp_raw else None
        if status_timestamp is None:
            status_timestamp = timezone.now()
        elif timezone.is_naive(status_timestamp):
            status_timestamp = timezone.make_aware(status_timestamp)
        update_kwargs = {
            "last_status": status,
            "last_error_code": error_code,
            "last_status_vendor_info": vendor_value,
            "last_status_timestamp": status_timestamp,
        }
        connector_value = payload_data.get("connectorId")
        await database_sync_to_async(persistence.update_status_notification_records)(
            charger_id=self.charger_id,
            connector_value=connector_value,
            primary_charger=self.charger,
            aggregate_charger=self.aggregate_charger,
            update_kwargs=update_kwargs,
        )
        try:
            await database_sync_to_async(persistence.sync_charger_error_security_event)(
                charger_id=self.charger_id,
                connector_value=connector_value,
                status=status,
                error_code=error_code,
                status_timestamp=status_timestamp,
            )
        except Exception:
            active_logger = getattr(self, "logger", logger)
            active_logger.exception(
                "Failed to sync charger security alert event for charger_id=%s connector=%s",
                self.charger_id,
                connector_value,
            )
        if status.lower() == "available":
            await self._handle_available_status_transition(self.connector_value)
        if self._is_auto_start_status(status):
            await self._send_configured_auto_start(evse_id=payload_data.get("evseId"))
        elif self._is_auto_start_connected_status(status):
            charger_pk = getattr(self.charger, "pk", None)
            if charger_pk:
                await database_sync_to_async(auto_start.mark_scope_started)(
                    charger_pk=charger_pk,
                    reservation_scope=self._auto_start_reservation_scope(
                        payload_data.get("evseId")
                    ),
                )
        else:
            await self._clear_auto_start_reservation(
                self._auto_start_reservation_scope(payload_data.get("evseId"))
            )
        store.add_log(
            self.store_key,
            f"StatusNotification processed: {json.dumps(payload_data, sort_keys=True)}",
            log_type="charger",
        )
        await self._sync_availability_state_from_status(
            status,
            status_timestamp,
            self.connector_value,
        )
        return {}

    async def _send_configured_auto_start(self, *, evse_id: object = None) -> None:
        """Send one remote start for the connector's plugged-in state."""

        reservation_scope = self._auto_start_reservation_scope(evse_id)
        id_tag = await self._configured_auto_start_id_tag()
        if not id_tag:
            await self._clear_auto_start_reservation(reservation_scope)
            return
        charger_pk = getattr(self.charger, "pk", None)
        if not charger_pk:
            return

        message_id = uuid.uuid4().hex
        ocpp_version = str(getattr(self, "ocpp_version", "") or "")
        ocpp_action = "RemoteStartTransaction"
        payload: dict[str, object] = {"idTag": id_tag}
        if ocpp_version.startswith(("ocpp2.0", "ocpp2.1")):
            ocpp_action = "RequestStartTransaction"
            payload = {
                "idToken": {"idToken": id_tag, "type": "Central"},
                "remoteStartId": int(uuid.uuid4().int % 1_000_000_000),
            }
            try:
                normalized_evse_id = int(evse_id)
            except (TypeError, ValueError):
                normalized_evse_id = None
            if normalized_evse_id and normalized_evse_id > 0:
                payload["evseId"] = normalized_evse_id
        elif self.connector_value is not None:
            payload["connectorId"] = self.connector_value

        attempt = await database_sync_to_async(auto_start.reserve_attempt)(
            charger_pk=charger_pk,
            reservation_scope=reservation_scope,
            id_tag=id_tag,
            message_id=message_id,
            action=ocpp_action,
        )
        if attempt is None:
            return

        try:
            await self.send(json.dumps([2, message_id, ocpp_action, payload]))
        except Exception:
            await database_sync_to_async(auto_start.transition_attempt)(
                attempt.attempt_id,
                state=AutoStartAttempt.State.FAILED,
                retry=True,
            )
            active_logger = getattr(self, "logger", logger)
            active_logger.exception(
                "Failed to send configured auto-start for charger_id=%s connector=%s",
                self.charger_id,
                self.connector_value,
            )
            return

        store.register_pending_call(
            message_id,
            {
                "action": ocpp_action,
                "charger_id": self.charger_id,
                "connector_id": self.connector_value,
                "log_key": self.store_key,
                "id_tag": id_tag,
                "auto_start_attempt_id": str(attempt.attempt_id),
                "requested_at": timezone.now(),
                "auto_start": True,
            },
        )
        store.schedule_call_timeout(
            message_id,
            timeout=auto_start.REQUEST_TIMEOUT.total_seconds(),
            action=ocpp_action,
            log_key=self.store_key,
            message=f"{ocpp_action} auto-start request timed out",
            on_timeout=lambda _metadata: (
                self._release_auto_start_reservation_on_timeout(str(attempt.attempt_id))
            ),
        )
        store.add_log(
            self.store_key,
            f"Auto-start requested after Preparing ({ocpp_action}).",
            log_type="charger",
        )

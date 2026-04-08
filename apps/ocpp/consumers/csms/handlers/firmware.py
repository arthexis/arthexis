"""Firmware-related inbound handlers for the CSMS consumer."""

from __future__ import annotations

import json
from datetime import datetime

from channels.db import database_sync_to_async
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.ocpp import store
from apps.ocpp.models import Charger, CPFirmwareDeployment
from apps.ocpp.utils import _parse_ocpp_timestamp
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel


class FirmwareHandlersMixin:
    """Handle inbound firmware status notifications and side effects."""

    async def _update_firmware_state(
        self, status: str, status_info: str, timestamp: datetime | None
    ) -> None:
        """Persist firmware status fields for the active charger identities."""

        targets: list[Charger] = []
        seen_ids: set[int] = set()
        for charger in (self.charger, self.aggregate_charger):
            if not charger or charger.pk is None:
                continue
            if charger.pk in seen_ids:
                continue
            targets.append(charger)
            seen_ids.add(charger.pk)

        if not targets:
            return

        def _persist(ids: list[int]) -> None:
            Charger.objects.filter(pk__in=ids).update(
                firmware_status=status,
                firmware_status_info=status_info,
                firmware_timestamp=timestamp,
            )

        await database_sync_to_async(_persist)([target.pk for target in targets])
        for target in targets:
            target.firmware_status = status
            target.firmware_status_info = status_info
            target.firmware_timestamp = timestamp

        def _update_deployments(ids: list[int]) -> None:
            deployments = list(
                CPFirmwareDeployment.objects.filter(
                    charger_id__in=ids, completed_at__isnull=True
                )
            )
            payload = {"status": status, "statusInfo": status_info}
            for deployment in deployments:
                deployment.mark_status(
                    status,
                    status_info,
                    timestamp,
                    response=payload,
                )

        await database_sync_to_async(_update_deployments)([target.pk for target in targets])

    @protocol_call(
        "ocpp21",
        ProtocolCallModel.CP_TO_CSMS,
        "PublishFirmwareStatusNotification",
    )
    @protocol_call(
        "ocpp201",
        ProtocolCallModel.CP_TO_CSMS,
        "PublishFirmwareStatusNotification",
    )
    async def _handle_publish_firmware_status_notification_action(
        self, payload, msg_id, raw, text_data
    ):
        status_raw = payload.get("status")
        status_value = str(status_raw or "").strip()
        info_value = payload.get("statusInfo")
        if not isinstance(info_value, str):
            info_value = payload.get("info")
        status_info = str(info_value or "").strip()
        request_id_value = payload.get("requestId")
        timestamp_value = _parse_ocpp_timestamp(payload.get("publishTimestamp"))
        if timestamp_value is None:
            timestamp_value = _parse_ocpp_timestamp(payload.get("timestamp"))
        if timestamp_value is None:
            timestamp_value = timezone.now()

        def _persist_status():
            deployment = None
            try:
                deployment_pk = int(request_id_value)
            except (TypeError, ValueError, OverflowError):
                deployment_pk = None
            if deployment_pk:
                deployment = CPFirmwareDeployment.objects.filter(pk=deployment_pk).first()
            if deployment is None and self.charger:
                deployment = (
                    CPFirmwareDeployment.objects.filter(
                        charger=self.charger,
                        completed_at__isnull=True,
                    )
                    .order_by("-requested_at")
                    .first()
                )
            if deployment is None:
                return
            if status_value == "Downloaded" and deployment.downloaded_at is None:
                deployment.downloaded_at = timestamp_value
            deployment.mark_status(
                status_value,
                status_info,
                timestamp_value,
                response=payload,
            )

        await database_sync_to_async(_persist_status)()
        self._log_ocpp201_notification("PublishFirmwareStatusNotification", payload)
        return {}

    @protocol_call("ocpp16", ProtocolCallModel.CP_TO_CSMS, "FirmwareStatusNotification")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "FirmwareStatusNotification")
    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "FirmwareStatusNotification")
    async def _handle_firmware_status_notification_action(
        self, payload, msg_id, raw, text_data
    ):
        status_raw = payload.get("status")
        status = str(status_raw or "").strip()
        info_value = payload.get("statusInfo")
        if not isinstance(info_value, str):
            info_value = payload.get("info")
        status_info = str(info_value or "").strip()
        timestamp_raw = payload.get("timestamp")
        timestamp_value = None
        if timestamp_raw:
            timestamp_value = parse_datetime(str(timestamp_raw))
            if timestamp_value and timezone.is_naive(timestamp_value):
                timestamp_value = timezone.make_aware(
                    timestamp_value, timezone.get_current_timezone()
                )
        if timestamp_value is None:
            timestamp_value = timezone.now()
        await self._update_firmware_state(status, status_info, timestamp_value)
        store.add_log(
            self.store_key,
            "FirmwareStatusNotification: " + json.dumps(payload, separators=(",", ":")),
            log_type="charger",
        )
        if self.aggregate_charger and self.aggregate_charger.connector_id is None:
            aggregate_key = store.identity_key(
                self.charger_id, self.aggregate_charger.connector_id
            )
            if aggregate_key != self.store_key:
                store.add_log(
                    aggregate_key,
                    "FirmwareStatusNotification: "
                    + json.dumps(payload, separators=(",", ":")),
                    log_type="charger",
                )
        return {}

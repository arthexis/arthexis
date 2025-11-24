from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Awaitable, Callable, Protocol

from channels.db import database_sync_to_async
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import store
from .models import (
    CPCertificate,
    CPCertificateOperation,
    CPFirmwareDeployment,
    CPNetworkProfileDeployment,
    CPReservation,
    ChargerConfiguration,
    Charger,
    ChargerLogRequest,
    DataTransferMessage,
    PowerProjection,
)


def _parse_ocpp_timestamp(value) -> datetime | None:
    """Return an aware :class:`~datetime.datetime` for OCPP timestamps."""

    if not value:
        return None
    if isinstance(value, datetime):
        timestamp = value
    else:
        timestamp = parse_datetime(str(value))
    if not timestamp:
        return None
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, timezone.get_current_timezone())
    return timestamp


class CallResultContext(Protocol):
    charger_id: str | None
    store_key: str
    charger: object | None
    aggregate_charger: object | None

    async def _update_local_authorization_state(self, version: int | None) -> None:
        ...

    async def _apply_local_authorization_entries(self, entries) -> int:
        ...

    async def _update_change_availability_state(
        self,
        connector_value: int | None,
        requested_type: str | None,
        status: str,
        requested_at,
        *,
        details: str = "",
    ) -> None:
        ...

    def _apply_change_configuration_snapshot(
        self, key: str, value: str | None, connector_hint: int | str | None
    ) -> ChargerConfiguration:
        ...

    def _persist_configuration_result(
        self, payload: dict, connector_id
    ) -> ChargerConfiguration | None:
        ...


CallResultHandler = Callable[
    [CallResultContext, str, dict, dict, str],
    Awaitable[bool],
]


async def handle_change_configuration_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    key_value = str(metadata.get("key") or "").strip()
    status_value = str(payload_data.get("status") or "").strip()
    stored_value = metadata.get("value")
    parts: list[str] = []
    if status_value:
        parts.append(f"status={status_value}")
    if key_value:
        parts.append(f"key={key_value}")
    if stored_value is not None:
        parts.append(f"value={stored_value}")
    message = "ChangeConfiguration result"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    if status_value.casefold() in {"accepted", "rebootrequired"} and key_value:
        connector_hint = metadata.get("connector_id")

        def _apply() -> ChargerConfiguration:
            return consumer._apply_change_configuration_snapshot(
                key_value,
                stored_value if isinstance(stored_value, str) else None,
                connector_hint,
            )

        configuration = await database_sync_to_async(_apply)()
        if configuration:
            if getattr(consumer, "charger", None) and getattr(
                consumer, "charger_id", None
            ):
                if getattr(consumer.charger, "charger_id", None) == consumer.charger_id:
                    consumer.charger.configuration = configuration
            if getattr(consumer, "aggregate_charger", None) and getattr(
                consumer, "charger_id", None
            ):
                if (
                    getattr(consumer.aggregate_charger, "charger_id", None)
                    == consumer.charger_id
                ):
                    consumer.aggregate_charger.configuration = configuration
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_data_transfer_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    message_pk = metadata.get("message_pk")
    if not message_pk:
        store.record_pending_call_result(
            message_id,
            metadata=metadata,
            payload=payload_data,
        )
        return True

    def _apply():
        message = (
            DataTransferMessage.objects.select_related("firmware_request")
            .filter(pk=message_pk)
            .first()
        )
        if not message:
            return
        status_value = str(payload_data.get("status") or "").strip()
        if not status_value:
            status_value = metadata.get("fallback_status") or "Unknown"
        timestamp = timezone.now()
        message.status = status_value
        message.response_data = (payload_data or {}).get("data")
        message.error_code = ""
        message.error_description = ""
        message.error_details = None
        message.responded_at = timestamp
        message.save(
            update_fields=[
                "status",
                "response_data",
                "error_code",
                "error_description",
                "error_details",
                "responded_at",
                "updated_at",
            ]
        )
        request = getattr(message, "firmware_request", None)
        if request:
            request.status = status_value
            request.responded_at = timestamp
            request.response_payload = payload_data
            request.save(
                update_fields=[
                    "status",
                    "responded_at",
                    "response_payload",
                    "updated_at",
                ]
            )

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_get_composite_schedule_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    projection_pk = metadata.get("projection_pk")
    status_value = str(payload_data.get("status") or "").strip()
    schedule_payload = payload_data.get("chargingSchedule") if isinstance(payload_data, dict) else {}
    schedule_start = _parse_ocpp_timestamp(payload_data.get("scheduleStart"))
    duration_value: int | None = None
    rate_unit_value = ""
    periods: list[dict[str, object]] = []
    if isinstance(schedule_payload, dict):
        try:
            duration_value = (
                int(schedule_payload.get("duration"))
                if schedule_payload.get("duration") is not None
                else None
            )
        except (TypeError, ValueError):
            duration_value = None
        rate_unit_value = str(schedule_payload.get("chargingRateUnit") or "").strip()
        raw_periods = schedule_payload.get("chargingSchedulePeriod")
        if isinstance(raw_periods, (list, tuple)):
            for entry in raw_periods:
                if not isinstance(entry, dict):
                    continue
                try:
                    start_period = int(entry.get("startPeriod"))
                except (TypeError, ValueError):
                    continue
                period: dict[str, object] = {
                    "start_period": start_period,
                    "limit": entry.get("limit"),
                }
                if entry.get("numberPhases") is not None:
                    period["number_phases"] = entry.get("numberPhases")
                if entry.get("phaseToUse") is not None:
                    period["phase_to_use"] = entry.get("phaseToUse")
                periods.append(period)

    def _apply() -> PowerProjection | None:
        if not projection_pk:
            return None
        projection = (
            PowerProjection.objects.filter(pk=projection_pk)
            .select_related("charger")
            .first()
        )
        if not projection:
            return None
        projection.status = status_value
        projection.schedule_start = schedule_start
        projection.duration_seconds = duration_value
        projection.charging_rate_unit = rate_unit_value
        projection.charging_schedule_periods = periods
        projection.raw_response = payload_data
        projection.received_at = timezone.now()
        projection.save(
            update_fields=[
                "status",
                "schedule_start",
                "duration_seconds",
                "charging_rate_unit",
                "charging_schedule_periods",
                "raw_response",
                "received_at",
                "updated_at",
            ]
        )
        return projection

    await database_sync_to_async(_apply)()

    message = "GetCompositeSchedule result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_get_log_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    request_pk = metadata.get("log_request_pk")
    capture_key = metadata.get("capture_key")
    status_value = str(payload_data.get("status") or "").strip()
    filename_value = str(
        payload_data.get("filename")
        or payload_data.get("location")
        or ""
    ).strip()
    location_value = str(payload_data.get("location") or "").strip()
    fragments: list[str] = []
    data_candidate = payload_data.get("logData") or payload_data.get("entries")
    if isinstance(data_candidate, (list, tuple)):
        for entry in data_candidate:
            if entry is None:
                continue
            if isinstance(entry, (bytes, bytearray)):
                try:
                    fragments.append(entry.decode("utf-8"))
                except Exception:
                    fragments.append(base64.b64encode(entry).decode("ascii"))
            else:
                fragments.append(str(entry))
    elif data_candidate not in (None, ""):
        fragments.append(str(data_candidate))

    def _update_request() -> str:
        request = None
        if request_pk:
            request = ChargerLogRequest.objects.filter(pk=request_pk).first()
        if request is None:
            return ""
        updates: dict[str, object] = {
            "responded_at": timezone.now(),
            "raw_response": payload_data,
        }
        if status_value:
            updates["status"] = status_value
        if filename_value:
            updates["filename"] = filename_value
        if location_value:
            updates["location"] = location_value
        if capture_key:
            updates["session_key"] = str(capture_key)
        message_identifier = metadata.get("message_id")
        if message_identifier:
            updates["message_id"] = str(message_identifier)
        ChargerLogRequest.objects.filter(pk=request.pk).update(**updates)
        for field, value in updates.items():
            setattr(request, field, value)
        return request.session_key or ""

    session_capture = await database_sync_to_async(_update_request)()
    message = "GetLog result"
    if status_value:
        message += f": status={status_value}"
    if filename_value:
        message += f", filename={filename_value}"
    if location_value:
        message += f", location={location_value}"
    store.add_log(log_key, message, log_type="charger")
    if capture_key and fragments:
        for fragment in fragments:
            store.append_log_capture(str(capture_key), fragment)
        store.finalize_log_capture(str(capture_key))
    elif session_capture and status_value.lower() in {
        "uploaded",
        "uploadfailure",
        "rejected",
        "idle",
    }:
        store.finalize_log_capture(session_capture)
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_send_local_list_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    version_candidate = (
        payload_data.get("currentLocalListVersion")
        or payload_data.get("listVersion")
        or metadata.get("list_version")
    )
    message = "SendLocalList result"
    if status_value:
        message += f": status={status_value}"
    if version_candidate is not None:
        message += f", version={version_candidate}"
    store.add_log(log_key, message, log_type="charger")
    version_int = None
    if version_candidate is not None:
        try:
            version_int = int(version_candidate)
        except (TypeError, ValueError):
            version_int = None
    await consumer._update_local_authorization_state(version_int)
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_get_local_list_version_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    version_candidate = payload_data.get("listVersion")
    processed = 0
    auth_list = payload_data.get("localAuthorizationList")
    if isinstance(auth_list, list):
        processed = await consumer._apply_local_authorization_entries(auth_list)
    message = "GetLocalListVersion result"
    if version_candidate is not None:
        message += f": version={version_candidate}"
    if processed:
        message += f", entries={processed}"
    store.add_log(log_key, message, log_type="charger")
    version_int = None
    if version_candidate is not None:
        try:
            version_int = int(version_candidate)
        except (TypeError, ValueError):
            version_int = None
    await consumer._update_local_authorization_state(version_int)
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_clear_cache_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "ClearCache result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    version_int = 0 if status_value == "Accepted" else None
    await consumer._update_local_authorization_state(version_int)
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_update_firmware_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    deployment_pk = metadata.get("deployment_pk")

    def _apply():
        if not deployment_pk:
            return
        deployment = CPFirmwareDeployment.objects.filter(pk=deployment_pk).first()
        if not deployment:
            return
        status_value = str(payload_data.get("status") or "").strip() or "Accepted"
        deployment.mark_status(
            status_value,
            "",
            timezone.now(),
            response=payload_data,
        )

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_get_configuration_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    try:
        payload_text = json.dumps(payload_data, sort_keys=True, ensure_ascii=False)
    except TypeError:
        payload_text = str(payload_data)
    store.add_log(
        log_key,
        f"GetConfiguration result: {payload_text}",
        log_type="charger",
    )
    configuration = await database_sync_to_async(consumer._persist_configuration_result)(
        payload_data, metadata.get("connector_id")
    )
    if configuration:
        if getattr(consumer, "charger", None) and getattr(consumer, "charger_id", None):
            if getattr(consumer.charger, "charger_id", None) == consumer.charger_id:
                consumer.charger.configuration = configuration
        if getattr(consumer, "aggregate_charger", None) and getattr(
            consumer, "charger_id", None
        ):
            if (
                getattr(consumer.aggregate_charger, "charger_id", None)
                == consumer.charger_id
            ):
                consumer.aggregate_charger.configuration = configuration
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_trigger_message_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    target = metadata.get("trigger_target") or metadata.get("follow_up_action")
    connector_value = metadata.get("trigger_connector")
    message = "TriggerMessage result"
    if target:
        message = f"TriggerMessage {target} result"
    if status_value:
        message += f": status={status_value}"
    if connector_value:
        message += f", connector={connector_value}"
    store.add_log(log_key, message, log_type="charger")
    if status_value == "Accepted" and target:
        store.register_triggered_followup(
            consumer.charger_id,
            str(target),
            connector=connector_value,
            log_key=log_key,
            target=str(target),
        )
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_reserve_now_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "ReserveNow result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")

    reservation_pk = metadata.get("reservation_pk")

    def _apply():
        if not reservation_pk:
            return
        reservation = CPReservation.objects.filter(pk=reservation_pk).first()
        if not reservation:
            return
        reservation.evcs_status = status_value
        reservation.evcs_error = ""
        confirmed = status_value.casefold() == "accepted"
        reservation.evcs_confirmed = confirmed
        reservation.evcs_confirmed_at = timezone.now() if confirmed else None
        reservation.save(
            update_fields=[
                "evcs_status",
                "evcs_error",
                "evcs_confirmed",
                "evcs_confirmed_at",
                "updated_on",
            ]
        )

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_cancel_reservation_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "CancelReservation result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")

    reservation_pk = metadata.get("reservation_pk")

    def _apply():
        if not reservation_pk:
            return
        reservation = CPReservation.objects.filter(pk=reservation_pk).first()
        if not reservation:
            return
        reservation.evcs_status = status_value
        reservation.evcs_error = ""
        reservation.evcs_confirmed = False
        reservation.evcs_confirmed_at = None
        reservation.save(
            update_fields=[
                "evcs_status",
                "evcs_error",
                "evcs_confirmed",
                "evcs_confirmed_at",
                "updated_on",
            ]
        )

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_remote_start_transaction_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "RemoteStartTransaction result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_remote_stop_transaction_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "RemoteStopTransaction result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_request_start_transaction_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "RequestStartTransaction result"
    if status_value:
        message += f": status={status_value}"
    tx_identifier = payload_data.get("transactionId")
    if tx_identifier:
        message += f", transactionId={tx_identifier}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_request_stop_transaction_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "RequestStopTransaction result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_reset_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "Reset result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_change_availability_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status = str((payload_data or {}).get("status") or "").strip()
    requested_type = metadata.get("availability_type")
    connector_value = metadata.get("connector_id")
    requested_at = metadata.get("requested_at")
    await consumer._update_change_availability_state(
        connector_value,
        requested_type,
        status,
        requested_at,
        details="",
    )
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_set_network_profile_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip() or "Accepted"
    timestamp_value = _parse_ocpp_timestamp(payload_data.get("timestamp"))
    deployment_pk = metadata.get("deployment_pk")
    status_timestamp = timestamp_value or timezone.now()

    def _apply():
        deployment = CPNetworkProfileDeployment.objects.select_related(
            "network_profile", "charger"
        ).filter(pk=deployment_pk)
        deployment_obj = deployment.first()
        if deployment_obj:
            deployment_obj.mark_status(
                status_value, "", status_timestamp, response=payload_data
            )
            deployment_obj.completed_at = timezone.now()
            deployment_obj.save(update_fields=["completed_at", "updated_at"])
            if status_value.casefold() == "accepted":
                Charger.objects.filter(pk=deployment_obj.charger_id).update(
                    network_profile=deployment_obj.network_profile
                )

    await database_sync_to_async(_apply)()
    message = "SetNetworkProfile result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_install_certificate_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip() or "Unknown"
    timestamp_value = _parse_ocpp_timestamp(payload_data.get("timestamp"))
    certificate_pk = metadata.get("certificate_pk")
    operation_pk = metadata.get("certificate_operation_pk")
    charger_pk = metadata.get("charger_pk")
    hash_data = metadata.get("certificate_hash_data")
    status_timestamp = timestamp_value or timezone.now()

    def _apply():
        certificate: CPCertificate | None = None
        if certificate_pk:
            certificate = CPCertificate.objects.filter(pk=certificate_pk).first()
        if not certificate and charger_pk:
            certificate = CPCertificate.objects.create(
                charger_id=charger_pk,
                status=status_value,
                status_timestamp=status_timestamp,
                last_seen_at=status_timestamp,
                is_user_data=True,
            )
        if certificate:
            if hash_data:
                certificate.apply_hash_data(hash_data)
            if charger_pk and not certificate.charger_id:
                certificate.charger_id = charger_pk
            certificate.status = status_value
            certificate.status_info = ""
            certificate.status_timestamp = status_timestamp
            certificate.last_seen_at = status_timestamp
            if status_value.casefold() == "accepted":
                certificate.installed_at = status_timestamp
                certificate.removed_at = None
            certificate.save(
                update_fields=[
                    "charger",
                    "certificate_type",
                    "serial_number",
                    "hash_algorithm",
                    "issuer_name_hash",
                    "subject_name_hash",
                    "public_key_hash",
                    "ocpp_hash_data",
                    "status",
                    "status_info",
                    "status_timestamp",
                    "installed_at",
                    "removed_at",
                    "last_seen_at",
                    "updated_at",
                ]
            )

        if operation_pk:
            operation = CPCertificateOperation.objects.filter(pk=operation_pk).first()
            if operation:
                operation.mark_status(status_value, "", status_timestamp, response=payload_data)
                operation.completed_at = timezone.now()
                operation.save(update_fields=["completed_at", "updated_at"])

    await database_sync_to_async(_apply)()
    message = "InstallCertificate result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_delete_certificate_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip() or "Unknown"
    timestamp_value = _parse_ocpp_timestamp(payload_data.get("timestamp"))
    certificate_pk = metadata.get("certificate_pk")
    operation_pk = metadata.get("certificate_operation_pk")
    status_timestamp = timestamp_value or timezone.now()

    def _apply():
        if certificate_pk:
            certificate = CPCertificate.objects.filter(pk=certificate_pk).first()
        else:
            certificate = None
        if certificate:
            certificate.status = status_value or "Deleted"
            certificate.status_info = ""
            certificate.status_timestamp = status_timestamp
            if status_value.casefold() == "accepted":
                certificate.removed_at = status_timestamp
            certificate.last_seen_at = status_timestamp
            certificate.save(
                update_fields=[
                    "status",
                    "status_info",
                    "status_timestamp",
                    "removed_at",
                    "last_seen_at",
                    "updated_at",
                ]
            )

        if operation_pk:
            operation = CPCertificateOperation.objects.filter(pk=operation_pk).first()
            if operation:
                operation.mark_status(status_value, "", status_timestamp, response=payload_data)
                operation.completed_at = timezone.now()
                operation.save(update_fields=["completed_at", "updated_at"])

    await database_sync_to_async(_apply)()
    message = "DeleteCertificate result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_get_installed_certificate_ids_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip() or "Unknown"
    certificate_hashes = payload_data.get("certificateHashData")
    timestamp_value = _parse_ocpp_timestamp(payload_data.get("timestamp"))
    operation_pk = metadata.get("certificate_operation_pk")
    charger_pk = metadata.get("charger_pk")
    status_timestamp = timestamp_value or timezone.now()

    def _apply_inventory():
        charger_obj: Charger | None = None
        if charger_pk:
            charger_obj = Charger.objects.filter(pk=charger_pk).first()
        elif getattr(consumer, "charger_id", None):
            charger_obj = Charger.objects.filter(
                charger_id=str(consumer.charger_id)
            ).first()

        if operation_pk:
            operation = CPCertificateOperation.objects.filter(pk=operation_pk).first()
            if operation:
                operation.mark_status(status_value, "", status_timestamp, response=payload_data)
                operation.completed_at = timezone.now()
                operation.save(update_fields=["completed_at", "updated_at"])
                if not charger_obj:
                    charger_obj = operation.charger

        if not charger_obj:
            return
        if status_value.casefold() != "accepted":
            return
        if not isinstance(certificate_hashes, list):
            return

        for entry in certificate_hashes:
            if not isinstance(entry, dict):
                continue
            certificate_type = str(entry.get("certificateType") or "").strip()
            serial_number = str(entry.get("serialNumber") or "").strip()
            certificate = CPCertificate.objects.filter(
                charger=charger_obj,
                certificate_type=certificate_type,
                serial_number=serial_number,
            ).first()
            if certificate is None:
                certificate = CPCertificate.objects.create(
                    charger=charger_obj,
                    certificate_type=certificate_type,
                    serial_number=serial_number,
                    status="Installed",
                    status_timestamp=status_timestamp,
                    installed_at=status_timestamp,
                    last_seen_at=status_timestamp,
                    ocpp_hash_data=entry,
                    is_user_data=True,
                )
            else:
                certificate.apply_hash_data(entry)
                certificate.status = "Installed"
                certificate.status_timestamp = status_timestamp
                certificate.installed_at = certificate.installed_at or status_timestamp
                certificate.removed_at = None
                certificate.last_seen_at = status_timestamp
                certificate.save(
                    update_fields=[
                        "certificate_type",
                        "serial_number",
                        "hash_algorithm",
                        "issuer_name_hash",
                        "subject_name_hash",
                        "public_key_hash",
                        "ocpp_hash_data",
                        "status",
                        "status_timestamp",
                        "installed_at",
                        "removed_at",
                        "last_seen_at",
                        "updated_at",
                    ]
                )

    await database_sync_to_async(_apply_inventory)()
    message = "GetInstalledCertificateIds result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


CALL_RESULT_HANDLERS: dict[str, CallResultHandler] = {
    "ChangeConfiguration": handle_change_configuration_result,
    "DataTransfer": handle_data_transfer_result,
    "GetCompositeSchedule": handle_get_composite_schedule_result,
    "GetLog": handle_get_log_result,
    "SendLocalList": handle_send_local_list_result,
    "GetLocalListVersion": handle_get_local_list_version_result,
    "ClearCache": handle_clear_cache_result,
    "UpdateFirmware": handle_update_firmware_result,
    "GetConfiguration": handle_get_configuration_result,
    "TriggerMessage": handle_trigger_message_result,
    "ReserveNow": handle_reserve_now_result,
    "CancelReservation": handle_cancel_reservation_result,
    "RemoteStartTransaction": handle_remote_start_transaction_result,
    "RemoteStopTransaction": handle_remote_stop_transaction_result,
    "RequestStartTransaction": handle_request_start_transaction_result,
    "RequestStopTransaction": handle_request_stop_transaction_result,
    "Reset": handle_reset_result,
    "ChangeAvailability": handle_change_availability_result,
    "SetNetworkProfile": handle_set_network_profile_result,
    "InstallCertificate": handle_install_certificate_result,
    "DeleteCertificate": handle_delete_certificate_result,
    "GetInstalledCertificateIds": handle_get_installed_certificate_ids_result,
}


async def dispatch_call_result(
    consumer: CallResultContext,
    action: str | None,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    if not action:
        return False
    handler = CALL_RESULT_HANDLERS.get(action)
    if not handler:
        return False
    return await handler(consumer, message_id, metadata, payload_data, log_key)

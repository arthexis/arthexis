from __future__ import annotations

import base64
import json
from typing import Awaitable, Callable, Protocol

from channels.db import database_sync_to_async
from django.utils import timezone
from .utils import _parse_ocpp_timestamp

from . import store
from .models import (
    CPFirmwareDeployment,
    CPNetworkProfileDeployment,
    CPReservation,
    ChargerConfiguration,
    Charger,
    ChargerLogRequest,
    DataTransferMessage,
    PowerProjection,
    CertificateOperation,
    InstalledCertificate,
    ChargingProfile,
    Variable,
    MonitoringRule,
)


def _format_status_info(status_info: object) -> str:
    if not status_info:
        return ""
    if isinstance(status_info, str):
        return status_info.strip()
    try:
        return json.dumps(status_info, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(status_info)


def _extract_component_variable(entry: dict) -> tuple[str, str, str, str]:
    component_data = entry.get("component")
    variable_data = entry.get("variable")
    if not isinstance(component_data, dict) or not isinstance(variable_data, dict):
        return "", "", "", ""
    component_name = str(component_data.get("name") or "").strip()
    component_instance = str(component_data.get("instance") or "").strip()
    variable_name = str(variable_data.get("name") or "").strip()
    variable_instance = str(variable_data.get("instance") or "").strip()
    return component_name, component_instance, variable_name, variable_instance
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


async def handle_publish_firmware_result(
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


async def handle_unpublish_firmware_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "UnpublishFirmware result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
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


async def handle_get_diagnostics_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    file_name = str(
        payload_data.get("fileName")
        or payload_data.get("filename")
        or ""
    ).strip()
    location_value = str(
        payload_data.get("location")
        or metadata.get("location")
        or ""
    ).strip()
    message = "GetDiagnostics result"
    if status_value:
        message += f": status={status_value}"
    if file_name:
        message += f", fileName={file_name}"
    if location_value:
        message += f", location={location_value}"
    store.add_log(log_key, message, log_type="charger")

    def _apply_updates():
        charger_id = metadata.get("charger_id")
        if not charger_id:
            return
        updates: dict[str, object] = {"diagnostics_timestamp": timezone.now()}
        if location_value:
            updates["diagnostics_location"] = location_value
        elif file_name:
            updates["diagnostics_location"] = file_name
        Charger.objects.filter(charger_id=charger_id).update(**updates)

    await database_sync_to_async(_apply_updates)()
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
    status_label = status_value.casefold()
    request_status = "accepted" if status_label == "accepted" else "rejected"
    store.update_transaction_request(
        message_id,
        status=request_status,
        transaction_id=tx_identifier,
    )
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
    status_label = status_value.casefold()
    request_status = "accepted" if status_label == "accepted" else "rejected"
    store.update_transaction_request(
        message_id,
        status=request_status,
        transaction_id=metadata.get("transaction_id"),
    )
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_get_transaction_status_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    ongoing = payload_data.get("ongoingIndicator")
    messages_in_queue = payload_data.get("messagesInQueue")
    parts: list[str] = []
    if ongoing is not None:
        parts.append(f"ongoingIndicator={ongoing}")
    if messages_in_queue is not None:
        parts.append(f"messagesInQueue={messages_in_queue}")
    message = "GetTransactionStatus result"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_set_charging_profile_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "SetChargingProfile result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_clear_charging_profile_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    status_info = _format_status_info(payload_data.get("statusInfo"))
    message = "ClearChargingProfile result"
    parts: list[str] = []
    if status_value:
        parts.append(f"status={status_value}")
    if status_info:
        parts.append(f"info={status_info}")
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")

    charging_profile_id = metadata.get("charging_profile_id")
    charger_id = metadata.get("charger_id")
    responded_at = timezone.now()

    def _apply_response() -> None:
        if not charging_profile_id:
            return
        qs = ChargingProfile.objects.filter(charging_profile_id=charging_profile_id)
        if charger_id:
            qs = qs.filter(charger__charger_id=str(charger_id))
        qs.update(
            last_status=status_value,
            last_status_info=status_info,
            last_response_payload=payload_data,
            last_response_at=responded_at,
        )

    await database_sync_to_async(_apply_response)()
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


async def handle_get_variables_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    results = payload_data.get("getVariableResult")
    if not isinstance(results, (list, tuple)):
        store.record_pending_call_result(
            message_id,
            metadata=metadata,
            payload=payload_data,
        )
        return True

    def _apply() -> None:
        charger_id = metadata.get("charger_id") or consumer.charger_id
        connector_id = metadata.get("connector_id")
        charger = None
        if charger_id:
            charger = Charger.objects.filter(
                charger_id=charger_id,
                connector_id=connector_id,
            ).first()
        if charger is None and charger_id:
            charger, _created = Charger.objects.get_or_create(
                charger_id=charger_id,
                connector_id=connector_id,
            )
        if charger is None:
            return
        for entry in results:
            if not isinstance(entry, dict):
                continue
            (
                component_name,
                component_instance,
                variable_name,
                variable_instance,
            ) = _extract_component_variable(entry)
            if not component_name or not variable_name:
                continue
            attribute_type = str(entry.get("attributeType") or "").strip()
            attribute_status = str(entry.get("attributeStatus") or "").strip()
            attribute_value = entry.get("attributeValue")
            value_text = str(attribute_value) if attribute_value is not None else ""
            Variable.objects.update_or_create(
                charger=charger,
                component_name=component_name,
                component_instance=component_instance,
                variable_name=variable_name,
                variable_instance=variable_instance,
                attribute_type=attribute_type,
                defaults={
                    "attribute_status": attribute_status,
                    "value": value_text,
                    "value_type": "",
                },
            )

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_set_variables_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    results = payload_data.get("setVariableResult")
    if not isinstance(results, (list, tuple)):
        store.record_pending_call_result(
            message_id,
            metadata=metadata,
            payload=payload_data,
        )
        return True
    request_entries = metadata.get("set_variable_data")
    if not isinstance(request_entries, (list, tuple)):
        request_entries = []
    request_lookup: dict[tuple[str, str, str, str, str], str] = {}
    for entry in request_entries:
        if not isinstance(entry, dict):
            continue
        (
            component_name,
            component_instance,
            variable_name,
            variable_instance,
        ) = _extract_component_variable(entry)
        if not component_name or not variable_name:
            continue
        attribute_type = str(entry.get("attributeType") or "").strip()
        attribute_value = entry.get("attributeValue")
        value_text = str(attribute_value) if attribute_value is not None else ""
        request_lookup[
            (
                component_name,
                component_instance,
                variable_name,
                variable_instance,
                attribute_type,
            )
        ] = value_text

    def _apply() -> None:
        charger_id = metadata.get("charger_id") or consumer.charger_id
        connector_id = metadata.get("connector_id")
        charger = None
        if charger_id:
            charger = Charger.objects.filter(
                charger_id=charger_id,
                connector_id=connector_id,
            ).first()
        if charger is None and charger_id:
            charger, _created = Charger.objects.get_or_create(
                charger_id=charger_id,
                connector_id=connector_id,
            )
        if charger is None:
            return
        for entry in results:
            if not isinstance(entry, dict):
                continue
            (
                component_name,
                component_instance,
                variable_name,
                variable_instance,
            ) = _extract_component_variable(entry)
            if not component_name or not variable_name:
                continue
            attribute_type = str(entry.get("attributeType") or "").strip()
            attribute_status = str(entry.get("attributeStatus") or "").strip()
            value_text = request_lookup.get(
                (
                    component_name,
                    component_instance,
                    variable_name,
                    variable_instance,
                    attribute_type,
                ),
                "",
            )
            Variable.objects.update_or_create(
                charger=charger,
                component_name=component_name,
                component_instance=component_instance,
                variable_name=variable_name,
                variable_instance=variable_instance,
                attribute_type=attribute_type,
                defaults={
                    "attribute_status": attribute_status,
                    "value": value_text,
                    "value_type": "",
                },
            )

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_set_variable_monitoring_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    results = payload_data.get("setMonitoringResult")
    if not isinstance(results, (list, tuple)):
        store.record_pending_call_result(
            message_id,
            metadata=metadata,
            payload=payload_data,
        )
        return True
    request_entries = metadata.get("set_monitoring_data")
    if not isinstance(request_entries, (list, tuple)):
        request_entries = []
    request_lookup: dict[int, dict[str, object]] = {}
    for entry in request_entries:
        if not isinstance(entry, dict):
            continue
        variable_monitoring = entry.get("variableMonitoring")
        if not isinstance(variable_monitoring, (list, tuple)):
            continue
        for monitor in variable_monitoring:
            if not isinstance(monitor, dict):
                continue
            monitoring_id_value = monitor.get("id") or monitor.get("monitoringId")
            try:
                monitoring_id = (
                    int(monitoring_id_value)
                    if monitoring_id_value is not None
                    else None
                )
            except (TypeError, ValueError):
                monitoring_id = None
            if monitoring_id is None:
                continue
            request_lookup[monitoring_id] = {
                "entry": entry,
                "monitor": monitor,
            }

    def _apply() -> None:
        charger_id = metadata.get("charger_id") or consumer.charger_id
        connector_id = metadata.get("connector_id")
        charger = None
        if charger_id:
            charger = Charger.objects.filter(
                charger_id=charger_id,
                connector_id=connector_id,
            ).first()
        if charger is None and charger_id:
            charger, _created = Charger.objects.get_or_create(
                charger_id=charger_id,
                connector_id=connector_id,
            )
        if charger is None:
            return
        for entry in results:
            if not isinstance(entry, dict):
                continue
            monitoring_id_value = entry.get("id") or entry.get("monitoringId")
            try:
                monitoring_id = (
                    int(monitoring_id_value)
                    if monitoring_id_value is not None
                    else None
                )
            except (TypeError, ValueError):
                monitoring_id = None
            if monitoring_id is None:
                continue
            status_value = str(entry.get("status") or "").strip()
            request_entry = request_lookup.get(monitoring_id)
            if not request_entry:
                continue
            component_name, component_instance, variable_name, variable_instance = _extract_component_variable(
                request_entry["entry"]
            )
            if not component_name or not variable_name:
                continue
            variable_obj, _created = Variable.objects.get_or_create(
                charger=charger,
                component_name=component_name,
                component_instance=component_instance,
                variable_name=variable_name,
                variable_instance=variable_instance,
                attribute_type="",
            )
            monitor = request_entry["monitor"]
            threshold_value = monitor.get("value")
            threshold_text = str(threshold_value) if threshold_value is not None else ""
            monitor_type = str(monitor.get("type") or "").strip()
            transaction_value = monitor.get("transaction")
            is_transaction = bool(transaction_value) if transaction_value is not None else False
            MonitoringRule.objects.update_or_create(
                charger=charger,
                monitoring_id=monitoring_id,
                defaults={
                    "variable": variable_obj,
                    "severity": monitor.get("severity"),
                    "monitor_type": monitor_type,
                    "threshold": threshold_text,
                    "is_transaction": is_transaction,
                    "is_active": status_value.casefold() == "accepted",
                    "raw_payload": monitor,
                },
            )

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_clear_variable_monitoring_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    monitor_ids = metadata.get("monitoring_ids")
    if not isinstance(monitor_ids, (list, tuple)):
        monitor_ids = []

    def _apply() -> None:
        if status_value.casefold() != "accepted":
            return
        charger_id = metadata.get("charger_id") or consumer.charger_id
        if not charger_id:
            return
        MonitoringRule.objects.filter(
            charger__charger_id=charger_id,
            monitoring_id__in=monitor_ids,
        ).update(is_active=False)

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_get_monitoring_report_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    request_id = metadata.get("request_id")
    if status_value.casefold() in {"rejected", "notsupported"} and request_id is not None:
        try:
            store.pop_monitoring_report_request(int(request_id))
        except (TypeError, ValueError):
            pass
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_set_monitoring_base_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    status_info_text = _format_status_info(payload_data.get("statusInfo"))
    monitoring_base = metadata.get("monitoring_base") or payload_data.get(
        "monitoringBase"
    )

    fragments: list[str] = []
    if status_value:
        fragments.append(f"status={status_value}")
    if status_info_text:
        fragments.append(f"statusInfo={status_info_text}")
    if monitoring_base not in (None, ""):
        fragments.append(f"base={monitoring_base}")
    message = "SetMonitoringBase result"
    if fragments:
        message += ": " + ", ".join(fragments)
    store.add_log(log_key, message, log_type="charger")

    result_metadata = dict(metadata or {})
    if monitoring_base not in (None, ""):
        result_metadata["monitoring_base"] = monitoring_base
    if status_value:
        result_metadata["status"] = status_value
    if status_info_text:
        result_metadata["status_info"] = status_info_text

    store.record_pending_call_result(
        message_id,
        metadata=result_metadata,
        payload=payload_data,
    )
    return True


async def handle_set_monitoring_level_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    status_info_text = _format_status_info(payload_data.get("statusInfo"))
    monitoring_level = metadata.get("monitoring_level") or payload_data.get(
        "severity"
    )

    fragments: list[str] = []
    if status_value:
        fragments.append(f"status={status_value}")
    if status_info_text:
        fragments.append(f"statusInfo={status_info_text}")
    if monitoring_level not in (None, ""):
        fragments.append(f"severity={monitoring_level}")
    message = "SetMonitoringLevel result"
    if fragments:
        message += ": " + ", ".join(fragments)
    store.add_log(log_key, message, log_type="charger")

    result_metadata = dict(metadata or {})
    if monitoring_level not in (None, ""):
        result_metadata["monitoring_level"] = monitoring_level
    if status_value:
        result_metadata["status"] = status_value
    if status_info_text:
        result_metadata["status_info"] = status_info_text

    store.record_pending_call_result(
        message_id,
        metadata=result_metadata,
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


async def handle_unlock_connector_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str((payload_data or {}).get("status") or "").strip()
    status_info_text = _format_status_info((payload_data or {}).get("statusInfo"))
    connector_value = metadata.get("connector_id")
    requested_at = metadata.get("requested_at")

    await consumer._update_change_availability_state(
        connector_value,
        None,
        status_value,
        requested_at,
        details=status_info_text,
    )

    result_metadata = dict(metadata or {})
    if status_value:
        result_metadata["status"] = status_value
    if status_info_text:
        result_metadata["status_info"] = status_info_text

    store.record_pending_call_result(
        message_id,
        metadata=result_metadata,
        payload=payload_data,
    )
    return True


async def handle_clear_display_message_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "ClearDisplayMessage result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_customer_information_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "CustomerInformation result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_get_base_report_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "GetBaseReport result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_get_charging_profiles_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "GetChargingProfiles result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_get_display_messages_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "GetDisplayMessages result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_get_report_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "GetReport result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_set_display_message_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip()
    message = "SetDisplayMessage result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
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
    status_info = _format_status_info(payload_data.get("statusInfo"))
    operation_pk = metadata.get("operation_pk")
    installed_pk = metadata.get("installed_certificate_pk")
    responded_at = timezone.now()

    def _apply():
        operation = CertificateOperation.objects.filter(pk=operation_pk).first()
        if operation:
            if status_value.casefold() == "accepted":
                operation.status = CertificateOperation.STATUS_ACCEPTED
            elif status_value.casefold() == "rejected":
                operation.status = CertificateOperation.STATUS_REJECTED
            else:
                operation.status = CertificateOperation.STATUS_ERROR
            operation.status_info = status_info
            operation.response_payload = payload_data
            operation.responded_at = responded_at
            operation.save(
                update_fields=["status", "status_info", "response_payload", "responded_at"]
            )
        installed = InstalledCertificate.objects.filter(pk=installed_pk).first()
        if installed:
            if status_value.casefold() == "accepted":
                installed.status = InstalledCertificate.STATUS_INSTALLED
                installed.installed_at = responded_at
            elif status_value.casefold() == "rejected":
                installed.status = InstalledCertificate.STATUS_REJECTED
            else:
                installed.status = InstalledCertificate.STATUS_ERROR
            installed.last_action = CertificateOperation.ACTION_INSTALL
            installed.save(update_fields=["status", "installed_at", "last_action"])

    await database_sync_to_async(_apply)()
    store.add_log(
        log_key,
        f"InstallCertificate result: status={status_value}",
        log_type="charger",
    )
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
    status_info = _format_status_info(payload_data.get("statusInfo"))
    operation_pk = metadata.get("operation_pk")
    installed_pk = metadata.get("installed_certificate_pk")
    responded_at = timezone.now()

    def _apply():
        operation = CertificateOperation.objects.filter(pk=operation_pk).first()
        if operation:
            if status_value.casefold() == "accepted":
                operation.status = CertificateOperation.STATUS_ACCEPTED
            elif status_value.casefold() == "rejected":
                operation.status = CertificateOperation.STATUS_REJECTED
            else:
                operation.status = CertificateOperation.STATUS_ERROR
            operation.status_info = status_info
            operation.response_payload = payload_data
            operation.responded_at = responded_at
            operation.save(
                update_fields=["status", "status_info", "response_payload", "responded_at"]
            )
        installed = InstalledCertificate.objects.filter(pk=installed_pk).first()
        if installed:
            if status_value.casefold() == "accepted":
                installed.status = InstalledCertificate.STATUS_DELETED
                installed.deleted_at = responded_at
            elif status_value.casefold() == "rejected":
                installed.status = InstalledCertificate.STATUS_REJECTED
            else:
                installed.status = InstalledCertificate.STATUS_ERROR
            installed.last_action = CertificateOperation.ACTION_DELETE
            installed.save(update_fields=["status", "deleted_at", "last_action"])

    await database_sync_to_async(_apply)()
    store.add_log(
        log_key,
        f"DeleteCertificate result: status={status_value}",
        log_type="charger",
    )
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_certificate_signed_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip() or "Unknown"
    status_info = _format_status_info(payload_data.get("statusInfo"))
    operation_pk = metadata.get("operation_pk")
    responded_at = timezone.now()

    def _apply():
        operation = CertificateOperation.objects.filter(pk=operation_pk).first()
        if operation:
            if status_value.casefold() == "accepted":
                operation.status = CertificateOperation.STATUS_ACCEPTED
            elif status_value.casefold() == "rejected":
                operation.status = CertificateOperation.STATUS_REJECTED
            else:
                operation.status = CertificateOperation.STATUS_ERROR
            operation.status_info = status_info
            operation.response_payload = payload_data
            operation.responded_at = responded_at
            operation.save(
                update_fields=["status", "status_info", "response_payload", "responded_at"]
            )

    await database_sync_to_async(_apply)()
    store.add_log(
        log_key,
        f"CertificateSigned result: status={status_value}",
        log_type="charger",
    )
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
    status_info = _format_status_info(payload_data.get("statusInfo"))
    operation_pk = metadata.get("operation_pk")
    charger_id = metadata.get("charger_id")
    responded_at = timezone.now()
    certificates = payload_data.get("certificateHashData") or []

    def _apply():
        operation = CertificateOperation.objects.filter(pk=operation_pk).first()
        if operation:
            if status_value.casefold() == "accepted":
                operation.status = CertificateOperation.STATUS_ACCEPTED
            elif status_value.casefold() == "rejected":
                operation.status = CertificateOperation.STATUS_REJECTED
            else:
                operation.status = CertificateOperation.STATUS_ERROR
            operation.status_info = status_info
            operation.response_payload = payload_data
            operation.responded_at = responded_at
            operation.save(
                update_fields=["status", "status_info", "response_payload", "responded_at"]
            )
        if status_value.casefold() != "accepted":
            return
        charger = Charger.objects.filter(charger_id=charger_id).first()
        if charger is None:
            return
        if isinstance(certificates, dict):
            entries = [certificates]
        elif isinstance(certificates, list):
            entries = certificates
        else:
            entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            hash_data = entry.get("hashData") or entry.get("certificateHashData") or entry
            if not isinstance(hash_data, dict):
                continue
            cert_type = str(entry.get("certificateType") or "").strip()
            installed, _created = InstalledCertificate.objects.get_or_create(
                charger=charger,
                certificate_hash_data=hash_data,
                defaults={
                    "certificate_type": cert_type,
                    "status": InstalledCertificate.STATUS_INSTALLED,
                    "last_action": CertificateOperation.ACTION_LIST,
                    "installed_at": responded_at,
                },
            )
            if not _created:
                installed.certificate_type = cert_type or installed.certificate_type
                installed.status = InstalledCertificate.STATUS_INSTALLED
                installed.last_action = CertificateOperation.ACTION_LIST
                if installed.installed_at is None:
                    installed.installed_at = responded_at
                installed.save(
                    update_fields=[
                        "certificate_type",
                        "status",
                        "last_action",
                        "installed_at",
                    ]
                )

    await database_sync_to_async(_apply)()
    store.add_log(
        log_key,
        f"GetInstalledCertificateIds result: status={status_value}",
        log_type="charger",
    )
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
    "PublishFirmware": handle_publish_firmware_result,
    "UnpublishFirmware": handle_unpublish_firmware_result,
    "GetConfiguration": handle_get_configuration_result,
    "TriggerMessage": handle_trigger_message_result,
    "ReserveNow": handle_reserve_now_result,
    "CancelReservation": handle_cancel_reservation_result,
    "RemoteStartTransaction": handle_remote_start_transaction_result,
    "RemoteStopTransaction": handle_remote_stop_transaction_result,
    "GetDiagnostics": handle_get_diagnostics_result,
    "RequestStartTransaction": handle_request_start_transaction_result,
    "RequestStopTransaction": handle_request_stop_transaction_result,
    "GetTransactionStatus": handle_get_transaction_status_result,
    "Reset": handle_reset_result,
    "ChangeAvailability": handle_change_availability_result,
    "UnlockConnector": handle_unlock_connector_result,
    "SetChargingProfile": handle_set_charging_profile_result,
    "ClearChargingProfile": handle_clear_charging_profile_result,
    "ClearDisplayMessage": handle_clear_display_message_result,
    "CustomerInformation": handle_customer_information_result,
    "GetBaseReport": handle_get_base_report_result,
    "GetChargingProfiles": handle_get_charging_profiles_result,
    "GetDisplayMessages": handle_get_display_messages_result,
    "GetReport": handle_get_report_result,
    "SetDisplayMessage": handle_set_display_message_result,
    "SetMonitoringBase": handle_set_monitoring_base_result,
    "SetMonitoringLevel": handle_set_monitoring_level_result,
    "SetNetworkProfile": handle_set_network_profile_result,
    "InstallCertificate": handle_install_certificate_result,
    "DeleteCertificate": handle_delete_certificate_result,
    "CertificateSigned": handle_certificate_signed_result,
    "GetInstalledCertificateIds": handle_get_installed_certificate_ids_result,
    "GetVariables": handle_get_variables_result,
    "SetVariables": handle_set_variables_result,
    "SetVariableMonitoring": handle_set_variable_monitoring_result,
    "ClearVariableMonitoring": handle_clear_variable_monitoring_result,
    "GetMonitoringReport": handle_get_monitoring_report_result,
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

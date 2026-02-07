from __future__ import annotations

import base64
import json

from channels.db import database_sync_to_async
from django.utils import timezone

from .. import store
from ..models import (
    Charger,
    ChargerConfiguration,
    ChargerLogRequest,
    DataTransferMessage,
    PowerProjection,
    Variable,
)
from ..utils import _parse_ocpp_timestamp
from .types import CallErrorContext, CallResultContext
from .utils import _format_status_info, _json_details


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


async def handle_get_configuration_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    parts: list[str] = []
    code_text = (error_code or "").strip()
    if code_text:
        parts.append(f"code={code_text}")
    description_text = (description or "").strip()
    if description_text:
        parts.append(f"description={description_text}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    if parts:
        message = "GetConfiguration error: " + ", ".join(parts)
    else:
        message = "GetConfiguration error"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_get_composite_schedule_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    projection_pk = metadata.get("projection_pk")

    def _apply_error():
        if not projection_pk:
            return
        projection = PowerProjection.objects.filter(pk=projection_pk).first()
        if not projection:
            return
        projection.status = error_code or "Error"
        projection.schedule_start = None
        projection.duration_seconds = None
        projection.charging_schedule_periods = []
        projection.raw_response = {
            "errorCode": error_code or "",
            "description": description or "",
            "details": details or {},
        }
        projection.received_at = timezone.now()
        projection.save(
            update_fields=[
                "status",
                "schedule_start",
                "duration_seconds",
                "charging_schedule_periods",
                "raw_response",
                "received_at",
                "updated_at",
            ]
        )

    await database_sync_to_async(_apply_error)()

    parts: list[str] = []
    if error_code:
        parts.append(f"code={error_code}")
    if description:
        parts.append(f"description={description}")
    message = "GetCompositeSchedule error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_change_configuration_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    key_value = str(metadata.get("key") or "").strip()
    parts: list[str] = []
    if key_value:
        parts.append(f"key={key_value}")
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    if details:
        parts.append(f"details={_json_details(details)}")
    message = "ChangeConfiguration error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_get_log_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    request_pk = metadata.get("log_request_pk")
    capture_key = metadata.get("capture_key")

    def _apply_error() -> None:
        if not request_pk:
            return
        request = ChargerLogRequest.objects.filter(pk=request_pk).first()
        if not request:
            return
        label = (error_code or "Error").strip() or "Error"
        request.status = label
        request.responded_at = timezone.now()
        request.raw_response = {
            "errorCode": error_code,
            "errorDescription": description,
            "details": details,
        }
        if capture_key:
            request.session_key = str(capture_key)
        request.save(
            update_fields=[
                "status",
                "responded_at",
                "raw_response",
                "session_key",
            ]
        )

    await database_sync_to_async(_apply_error)()
    parts: list[str] = []
    if error_code:
        parts.append(f"code={error_code}")
    if description:
        parts.append(f"description={description}")
    message = "GetLog error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    if capture_key:
        store.finalize_log_capture(str(capture_key))
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_data_transfer_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    message_pk = metadata.get("message_pk")
    if not message_pk:
        store.record_pending_call_result(
            message_id,
            metadata=metadata,
            success=False,
            error_code=error_code,
            error_description=description,
            error_details=details,
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
        status_value = (error_code or "Error").strip() or "Error"
        timestamp = timezone.now()
        message.status = status_value
        message.response_data = None
        message.error_code = (error_code or "").strip()
        message.error_description = (description or "").strip()
        message.error_details = details
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
            request.response_payload = {
                "errorCode": error_code,
                "errorDescription": description,
                "details": details,
            }
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
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_clear_cache_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    parts: list[str] = []
    code_text = (error_code or "").strip()
    if code_text:
        parts.append(f"code={code_text}")
    description_text = (description or "").strip()
    if description_text:
        parts.append(f"description={description_text}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    message = "ClearCache error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    await consumer._update_local_authorization_state(None)
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_trigger_message_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    target = metadata.get("trigger_target") or metadata.get("follow_up_action")
    connector_value = metadata.get("trigger_connector")
    parts: list[str] = []
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    if details:
        parts.append("details=" + _json_details(details))
    label = f"TriggerMessage {target}" if target else "TriggerMessage"
    message = label + " error"
    if parts:
        message += ": " + ", ".join(parts)
    if connector_value:
        message += f", connector={connector_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_remote_start_transaction_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    message = "RemoteStartTransaction error"
    if error_code:
        message += f": code={str(error_code).strip()}"
    if description:
        suffix = str(description).strip()
        if suffix:
            message += f", description={suffix}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_remote_stop_transaction_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    message = "RemoteStopTransaction error"
    if error_code:
        message += f": code={str(error_code).strip()}"
    if description:
        suffix = str(description).strip()
        if suffix:
            message += f", description={suffix}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_get_diagnostics_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    parts: list[str] = []
    code_text = (error_code or "").strip()
    description_text = (description or "").strip()
    if code_text:
        parts.append(f"code={code_text}")
    if description_text:
        parts.append(f"description={description_text}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    message = "GetDiagnostics error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_request_start_transaction_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    message = "RequestStartTransaction error"
    if error_code:
        message += f": code={str(error_code).strip()}"
    if description:
        suffix = str(description).strip()
        if suffix:
            message += f", description={suffix}"
    store.add_log(log_key, message, log_type="charger")
    store.update_transaction_request(message_id, status="rejected")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_request_stop_transaction_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    message = "RequestStopTransaction error"
    if error_code:
        message += f": code={str(error_code).strip()}"
    if description:
        suffix = str(description).strip()
        if suffix:
            message += f", description={suffix}"
    store.add_log(log_key, message, log_type="charger")
    store.update_transaction_request(message_id, status="rejected")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_get_transaction_status_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    message = "GetTransactionStatus error"
    if error_code:
        message += f": code={str(error_code).strip()}"
    if description:
        suffix = str(description).strip()
        if suffix:
            message += f", description={suffix}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_reset_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    message = "Reset error"
    if error_code:
        message += f": code={str(error_code).strip()}"
    if description:
        suffix = str(description).strip()
        if suffix:
            message += f", description={suffix}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_change_availability_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    detail_text = _json_details(details) or (description or "").strip() or (error_code or "").strip() or "Error"
    requested_type = metadata.get("availability_type")
    connector_value = metadata.get("connector_id")
    requested_at = metadata.get("requested_at")
    await consumer._update_change_availability_state(
        connector_value,
        requested_type,
        "Rejected",
        requested_at,
        details=detail_text,
    )
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_unlock_connector_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    detail_text = _json_details(details) if details is not None else ""
    if not detail_text:
        detail_text = (description or "").strip()
    if not detail_text:
        detail_text = (error_code or "").strip() or "Error"

    connector_value = metadata.get("connector_id")
    requested_at = metadata.get("requested_at")
    await consumer._update_change_availability_state(
        connector_value,
        None,
        "Rejected",
        requested_at,
        details=detail_text,
    )

    parts: list[str] = []
    if error_code:
        parts.append(f"code={error_code}")
    if description:
        parts.append(f"description={description}")
    if details:
        parts.append(f"details={_json_details(details)}")
    message = "UnlockConnector error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")

    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_clear_display_message_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    parts: list[str] = []
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    message = "ClearDisplayMessage error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_customer_information_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    parts: list[str] = []
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    message = "CustomerInformation error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_get_base_report_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    parts: list[str] = []
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    message = "GetBaseReport error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_get_display_messages_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    parts: list[str] = []
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    message = "GetDisplayMessages error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_get_report_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    parts: list[str] = []
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    message = "GetReport error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_set_display_message_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    parts: list[str] = []
    if error_code:
        parts.append(f"code={str(error_code).strip()}")
    if description:
        parts.append(f"description={str(description).strip()}")
    details_text = _json_details(details)
    if details_text:
        parts.append(f"details={details_text}")
    message = "SetDisplayMessage error"
    if parts:
        message += ": " + ", ".join(parts)
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True

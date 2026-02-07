from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from .. import store
from ..models import Charger, MonitoringRule, Variable
from .types import CallErrorContext, CallResultContext
from .utils import _extract_component_variable, _format_status_info, _json_details


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


async def handle_set_monitoring_base_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    monitoring_base = metadata.get("monitoring_base")
    fragments: list[str] = []
    if error_code:
        fragments.append(f"code={str(error_code).strip()}")
    if description:
        fragments.append(f"description={str(description).strip()}")
    details_text = _json_details(details)
    if details_text:
        fragments.append(f"details={details_text}")
    if monitoring_base not in (None, ""):
        fragments.append(f"base={monitoring_base}")

    message = "SetMonitoringBase error"
    if fragments:
        message += ": " + ", ".join(fragments)
    store.add_log(log_key, message, log_type="charger")

    result_metadata = dict(metadata or {})
    if monitoring_base not in (None, ""):
        result_metadata["monitoring_base"] = monitoring_base

    store.record_pending_call_result(
        message_id,
        metadata=result_metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_set_monitoring_level_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    monitoring_level = metadata.get("monitoring_level")
    fragments: list[str] = []
    if error_code:
        fragments.append(f"code={str(error_code).strip()}")
    if description:
        fragments.append(f"description={str(description).strip()}")
    details_text = _json_details(details)
    if details_text:
        fragments.append(f"details={details_text}")
    if monitoring_level not in (None, ""):
        fragments.append(f"severity={monitoring_level}")

    message = "SetMonitoringLevel error"
    if fragments:
        message += ": " + ", ".join(fragments)
    store.add_log(log_key, message, log_type="charger")

    result_metadata = dict(metadata or {})
    if monitoring_level not in (None, ""):
        result_metadata["monitoring_level"] = monitoring_level

    store.record_pending_call_result(
        message_id,
        metadata=result_metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True

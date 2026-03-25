"""Repository-style persistence helpers for CSMS action handlers."""

from __future__ import annotations

from datetime import datetime

from apps.ocpp.models import (
    ClearedChargingLimitEvent,
    DisplayMessage,
    DisplayMessageNotification,
    MonitoringReport,
    MonitoringRule,
    Variable,
)
from apps.ocpp.services.charger_resolution import resolve_charger_target


def persist_cleared_charging_limit_event(
    *,
    charger,
    aggregate_charger,
    charger_id: str | None,
    connector_id: int | None,
    msg_id: str | None,
    evse_id: int | None,
    source_value: str,
    payload_data: dict,
) -> None:
    target = resolve_charger_target(
        charger=charger,
        aggregate_charger=aggregate_charger,
        charger_id=charger_id,
        connector_id=connector_id,
    )
    if target is None:
        return

    ClearedChargingLimitEvent.objects.create(
        charger=target,
        ocpp_message_id=msg_id or "",
        evse_id=evse_id,
        charging_limit_source=source_value,
        raw_payload=payload_data,
    )


def persist_notify_charging_limit(
    *,
    charger,
    aggregate_charger,
    charger_id: str | None,
    connector_id: int | None,
    normalized_payload: dict[str, object],
    source_value: str,
    grid_critical: bool | None,
    received_at,
) -> None:
    target = resolve_charger_target(
        charger=charger,
        aggregate_charger=aggregate_charger,
        charger_id=charger_id,
        connector_id=connector_id,
    )
    if target is None:
        return

    updates: dict[str, object] = {
        "last_charging_limit": normalized_payload,
        "last_charging_limit_source": source_value,
        "last_charging_limit_at": received_at,
    }
    if grid_critical is not None:
        updates["last_charging_limit_is_grid_critical"] = grid_critical

    target.__class__.objects.filter(pk=target.pk).update(**updates)
    for field, value in updates.items():
        setattr(target, field, value)


def persist_notify_display_messages(
    *,
    charger,
    aggregate_charger,
    charger_id: str | None,
    connector_id: int | None,
    msg_id: str | None,
    request_id: int | None,
    tbc: bool,
    payload_data: dict,
    message_info: list,
    received_at,
    parse_timestamp,
) -> list[dict[str, object]]:
    target = resolve_charger_target(
        charger=charger,
        aggregate_charger=aggregate_charger,
        charger_id=charger_id,
        connector_id=connector_id,
    )
    if target is None:
        return []

    notification = None
    if request_id is not None:
        notification = (
            DisplayMessageNotification.objects.filter(
                charger=target,
                request_id=request_id,
                completed_at__isnull=True,
            )
            .order_by("-received_at")
            .first()
        )
    if notification is None:
        notification = DisplayMessageNotification.objects.create(
            charger=target,
            ocpp_message_id=msg_id or "",
            request_id=request_id,
            tbc=tbc,
            raw_payload=payload_data,
        )

    updates: dict[str, object] = {"tbc": tbc}
    if not tbc:
        updates["completed_at"] = received_at
    DisplayMessageNotification.objects.filter(pk=notification.pk).update(**updates)
    for field, value in updates.items():
        setattr(notification, field, value)

    compliance_messages: list[dict[str, object]] = []
    for entry in message_info:
        if not isinstance(entry, dict):
            continue
        message_id_value = entry.get("messageId")
        try:
            message_id = int(message_id_value) if message_id_value is not None else None
        except (TypeError, ValueError):
            message_id = None
        message_payload = entry.get("message") or {}
        if not isinstance(message_payload, dict):
            message_payload = {}
        content_value = (
            message_payload.get("content")
            or message_payload.get("text")
            or entry.get("content")
            or ""
        )
        language_value = message_payload.get("language") or entry.get("language") or ""
        component = entry.get("component") or {}
        variable = entry.get("variable") or {}
        if not isinstance(component, dict):
            component = {}
        if not isinstance(variable, dict):
            variable = {}

        compliance_messages.append(
            {
                "message_id": message_id,
                "priority": str(entry.get("priority") or ""),
                "state": str(entry.get("state") or ""),
                "valid_from": parse_timestamp(entry.get("validFrom")),
                "valid_to": parse_timestamp(entry.get("validTo")),
                "language": str(language_value or ""),
                "content": str(content_value or ""),
            }
        )

        DisplayMessage.objects.create(
            notification=notification,
            charger=target,
            message_id=message_id,
            priority=str(entry.get("priority") or ""),
            state=str(entry.get("state") or ""),
            valid_from=parse_timestamp(entry.get("validFrom")),
            valid_to=parse_timestamp(entry.get("validTo")),
            language=str(language_value or ""),
            content=str(content_value or ""),
            component_name=str(component.get("name") or ""),
            component_instance=str(component.get("instance") or ""),
            variable_name=str(variable.get("name") or ""),
            variable_instance=str(variable.get("instance") or ""),
            raw_payload=entry,
        )

    return compliance_messages


def persist_notify_monitoring_report(
    *,
    charger,
    aggregate_charger,
    charger_id: str | None,
    connector_id: int | None,
    request_id: int | None,
    seq_no: int | None,
    generated_at: datetime | None,
    tbc: bool,
    payload_data: dict,
    monitoring_data: list,
) -> list[dict[str, object]]:
    target = resolve_charger_target(
        charger=charger,
        aggregate_charger=aggregate_charger,
        charger_id=charger_id,
        connector_id=connector_id,
    )
    if target is None:
        return []

    MonitoringReport.objects.create(
        charger=target,
        request_id=request_id,
        seq_no=seq_no,
        generated_at=generated_at,
        tbc=tbc,
        raw_payload=payload_data,
    )

    normalized_records: list[dict[str, object]] = []
    for entry in monitoring_data:
        if not isinstance(entry, dict):
            continue
        component_data = entry.get("component")
        variable_data = entry.get("variable")
        if not isinstance(component_data, dict) or not isinstance(variable_data, dict):
            continue

        component_name = str(component_data.get("name") or "").strip()
        variable_name = str(variable_data.get("name") or "").strip()
        if not component_name or not variable_name:
            continue

        component_instance = str(component_data.get("instance") or "").strip()
        variable_instance = str(variable_data.get("instance") or "").strip()
        component_evse = component_data.get("evse")
        evse_id = None
        connector_hint = None
        if isinstance(component_evse, dict):
            try:
                evse_id = int(component_evse.get("id"))
            except (TypeError, ValueError):
                evse_id = None
            connector_hint = component_evse.get("connectorId")

        variable_obj, _created = Variable.objects.get_or_create(
            charger=target,
            component_name=component_name,
            component_instance=component_instance,
            variable_name=variable_name,
            variable_instance=variable_instance,
            attribute_type="",
        )

        variable_monitoring = entry.get("variableMonitoring")
        if not isinstance(variable_monitoring, (list, tuple)):
            continue

        for monitor in variable_monitoring:
            if not isinstance(monitor, dict):
                continue
            monitoring_id_value = monitor.get("id") or monitor.get("monitoringId")
            try:
                monitoring_id = (
                    int(monitoring_id_value) if monitoring_id_value is not None else None
                )
            except (TypeError, ValueError):
                monitoring_id = None
            if monitoring_id is None:
                continue

            severity_value = monitor.get("severity")
            try:
                severity = int(severity_value) if severity_value is not None else None
            except (TypeError, ValueError):
                severity = None
            threshold_value = monitor.get("value")
            threshold_text = str(threshold_value) if threshold_value is not None else ""
            monitor_type = str(monitor.get("type") or "").strip()
            transaction_value = monitor.get("transaction")
            is_transaction = bool(transaction_value) if transaction_value is not None else False

            MonitoringRule.objects.update_or_create(
                charger=target,
                monitoring_id=monitoring_id,
                defaults={
                    "variable": variable_obj,
                    "severity": severity,
                    "monitor_type": monitor_type,
                    "threshold": threshold_text,
                    "is_transaction": is_transaction,
                    "is_active": True,
                    "raw_payload": monitor,
                },
            )

            normalized_records.append(
                {
                    "charger_id": target.charger_id,
                    "request_id": request_id,
                    "seq_no": seq_no,
                    "generated_at": generated_at,
                    "tbc": tbc,
                    "component_name": component_name,
                    "component_instance": component_instance,
                    "variable_name": variable_name,
                    "variable_instance": variable_instance,
                    "monitoring_id": monitoring_id,
                    "severity": severity,
                    "monitor_type": monitor_type,
                    "threshold": threshold_text,
                    "is_transaction": is_transaction,
                    "evse_id": evse_id,
                    "connector_id": connector_hint,
                }
            )

    return normalized_records

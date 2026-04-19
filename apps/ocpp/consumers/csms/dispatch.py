"""Action dispatch registry for CSMS consumer handlers."""

from __future__ import annotations

from typing import Protocol, cast

from apps.ocpp.consumers.csms.actions import build_action_handlers
from apps.ocpp.payload_types import Handler, SupportsHandle


class ConsumerDispatchContext(Protocol):
    _handle_authorize_action: Handler
    _handle_boot_notification_action: Handler
    _handle_cost_updated_action: Handler
    _handle_data_transfer_action: Handler
    _handle_diagnostics_status_notification_action: Handler
    _handle_firmware_status_notification_action: Handler
    _handle_get_15118_ev_certificate_action: Handler
    _handle_get_certificate_status_action: Handler
    _handle_heartbeat_action: Handler
    _handle_log_status_notification_action: Handler
    _handle_meter_values_action: Handler
    _handle_notify_customer_information_action: Handler
    _handle_notify_ev_charging_needs_action: Handler
    _handle_notify_ev_charging_schedule_action: Handler
    _handle_notify_event_action: Handler
    _handle_notify_report_action: Handler
    _handle_publish_firmware_status_notification_action: Handler
    _handle_report_charging_profiles_action: Handler
    _handle_reservation_status_update_action: Handler
    _handle_security_event_notification_action: Handler
    _handle_sign_certificate_action: Handler
    _handle_start_transaction_action: Handler
    _handle_status_notification_action: Handler
    _handle_stop_transaction_action: Handler
    _handle_transaction_event_action: Handler


def build_action_registry(consumer: ConsumerDispatchContext) -> dict[str, Handler]:
    """Return action routing map while preserving legacy/2.0.1 behaviour."""

    c = consumer
    handlers = cast(dict[str, SupportsHandle], build_action_handlers(consumer))
    return {
        "Authorize": c._handle_authorize_action,
        "BootNotification": c._handle_boot_notification_action,
        "ClearedChargingLimit": handlers["ClearedChargingLimit"].handle,
        "CostUpdated": c._handle_cost_updated_action,
        "DataTransfer": c._handle_data_transfer_action,
        "DiagnosticsStatusNotification": c._handle_diagnostics_status_notification_action,
        "FirmwareStatusNotification": c._handle_firmware_status_notification_action,
        "Get15118EVCertificate": c._handle_get_15118_ev_certificate_action,
        "GetCertificateStatus": c._handle_get_certificate_status_action,
        "Heartbeat": c._handle_heartbeat_action,
        "LogStatusNotification": c._handle_log_status_notification_action,
        "MeterValues": c._handle_meter_values_action,
        "NotifyChargingLimit": handlers["NotifyChargingLimit"].handle,
        "NotifyCustomerInformation": c._handle_notify_customer_information_action,
        "NotifyDisplayMessages": handlers["NotifyDisplayMessages"].handle,
        "NotifyEVChargingNeeds": c._handle_notify_ev_charging_needs_action,
        "NotifyEVChargingSchedule": c._handle_notify_ev_charging_schedule_action,
        "NotifyEvent": c._handle_notify_event_action,
        "NotifyMonitoringReport": handlers["NotifyMonitoringReport"].handle,
        "NotifyReport": c._handle_notify_report_action,
        "PublishFirmwareStatusNotification": c._handle_publish_firmware_status_notification_action,
        "ReportChargingProfiles": c._handle_report_charging_profiles_action,
        "ReservationStatusUpdate": c._handle_reservation_status_update_action,
        "SecurityEventNotification": c._handle_security_event_notification_action,
        "SignCertificate": c._handle_sign_certificate_action,
        "StartTransaction": c._handle_start_transaction_action,
        "StatusNotification": c._handle_status_notification_action,
        "StopTransaction": c._handle_stop_transaction_action,
        "TransactionEvent": c._handle_transaction_event_action,
    }

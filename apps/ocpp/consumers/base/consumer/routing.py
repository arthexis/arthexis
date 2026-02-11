"""Action routing helpers for inbound OCPP Call messages.

This module centralizes the action-to-handler registry used for OCPP 1.6 and
OCPP 2.x call dispatch. Routing itself does not perform DB writes; side effects
occur only inside delegated handlers on the consumer.
"""

from collections.abc import Awaitable, Callable
from typing import Any


Handler = Callable[[dict[str, Any], str, str | None, str | None], Awaitable[dict]]


class ActionRouter:
    """Explicit action registry for :class:`CSMSConsumer`.

    The router assumes OCPP Call frames are already validated and normalized by
    the websocket dispatch layer. It returns bound coroutine handlers on the
    consumer, where DB side effects (transactions, meter persistence, etc.)
    occur.
    """

    def __init__(self, consumer: Any) -> None:
        self.consumer = consumer
        self._handlers: dict[str, Handler] = self._build_registry()

    def _build_registry(self) -> dict[str, Handler]:
        c = self.consumer
        return {
            "BootNotification": c._handle_boot_notification_action,
            "DataTransfer": c._handle_data_transfer_action,
            "Heartbeat": c._handle_heartbeat_action,
            "StatusNotification": c._handle_status_notification_action,
            "Authorize": c._handle_authorize_action,
            "MeterValues": c._handle_meter_values_action,
            "TransactionEvent": c._handle_transaction_event_action,
            "SecurityEventNotification": c._handle_security_event_notification_action,
            "NotifyChargingLimit": c._handle_notify_charging_limit_action,
            "ClearedChargingLimit": c._handle_cleared_charging_limit_action,
            "NotifyCustomerInformation": c._handle_notify_customer_information_action,
            "NotifyDisplayMessages": c._handle_notify_display_messages_action,
            "NotifyEVChargingNeeds": c._handle_notify_ev_charging_needs_action,
            "NotifyEVChargingSchedule": c._handle_notify_ev_charging_schedule_action,
            "NotifyEvent": c._handle_notify_event_action,
            "NotifyMonitoringReport": c._handle_notify_monitoring_report_action,
            "NotifyReport": c._handle_notify_report_action,
            "CostUpdated": c._handle_cost_updated_action,
            "PublishFirmwareStatusNotification": c._handle_publish_firmware_status_notification_action,
            "ReportChargingProfiles": c._handle_report_charging_profiles_action,
            "DiagnosticsStatusNotification": c._handle_diagnostics_status_notification_action,
            "LogStatusNotification": c._handle_log_status_notification_action,
            "StartTransaction": c._handle_start_transaction_action,
            "StopTransaction": c._handle_stop_transaction_action,
            "FirmwareStatusNotification": c._handle_firmware_status_notification_action,
            "ReservationStatusUpdate": c._handle_reservation_status_update_action,
            "Get15118EVCertificate": c._handle_get_15118_ev_certificate_action,
            "GetCertificateStatus": c._handle_get_certificate_status_action,
            "SignCertificate": c._handle_sign_certificate_action,
        }

    def resolve(self, action: str) -> Handler | None:
        """Return the bound handler for the OCPP action name."""

        return self._handlers.get(action)

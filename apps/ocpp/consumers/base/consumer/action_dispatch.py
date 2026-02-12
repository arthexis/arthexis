"""Action dispatch registry for inbound OCPP Call messages.

The registry maps OCPP action names to callables on the active consumer
instance. Routing is protocol-version agnostic; version-specific behavior
belongs inside the destination handlers. This keeps OCPP 1.6 vs 2.x boundaries
localized to individual action implementations rather than dispatch plumbing.

Public extension points:
    * ``build_action_registry`` for replacing/augmenting action mappings.
    * ``ActionDispatchRegistry.resolve`` for custom dispatch wrappers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import CSMSConsumer

ActionHandler = Callable[[dict[str, Any], str, str | None, str | None], Awaitable[dict]]


def build_action_registry(consumer: "CSMSConsumer") -> dict[str, ActionHandler]:
    """Return the default mapping from action name to bound handler."""

    c = consumer
    return {
        "Authorize": c._handle_authorize_action,
        "BootNotification": c._handle_boot_notification_action,
        "ClearedChargingLimit": c._handle_cleared_charging_limit_action,
        "CostUpdated": c._handle_cost_updated_action,
        "DataTransfer": c._handle_data_transfer_action,
        "DiagnosticsStatusNotification": c._handle_diagnostics_status_notification_action,
        "FirmwareStatusNotification": c._handle_firmware_status_notification_action,
        "Get15118EVCertificate": c._handle_get_15118_ev_certificate_action,
        "GetCertificateStatus": c._handle_get_certificate_status_action,
        "Heartbeat": c._handle_heartbeat_action,
        "LogStatusNotification": c._handle_log_status_notification_action,
        "MeterValues": c._handle_meter_values_action,
        "NotifyChargingLimit": c._handle_notify_charging_limit_action,
        "NotifyCustomerInformation": c._handle_notify_customer_information_action,
        "NotifyDisplayMessages": c._handle_notify_display_messages_action,
        "NotifyEVChargingNeeds": c._handle_notify_ev_charging_needs_action,
        "NotifyEVChargingSchedule": c._handle_notify_ev_charging_schedule_action,
        "NotifyEvent": c._handle_notify_event_action,
        "NotifyMonitoringReport": c._handle_notify_monitoring_report_action,
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


class ActionDispatchRegistry:
    """Registry wrapper that resolves action handlers by name."""

    def __init__(self, consumer: "CSMSConsumer") -> None:
        self._handlers = build_action_registry(consumer)

    def resolve(self, action: str) -> ActionHandler | None:
        """Return the registered handler for ``action``, if available."""

        return self._handlers.get(action)

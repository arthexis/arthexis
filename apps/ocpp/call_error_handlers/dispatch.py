"""Dispatcher and registry for OCPP call error handlers."""

from __future__ import annotations

import logging
from types import MappingProxyType

from .certificates import (
    handle_certificate_signed_error,
    handle_delete_certificate_error,
    handle_get_installed_certificate_ids_error,
    handle_install_certificate_error,
)
from .configuration import (
    handle_change_availability_error,
    handle_change_configuration_error,
    handle_clear_cache_error,
    handle_clear_display_message_error,
    handle_customer_information_error,
    handle_get_base_report_error,
    handle_get_configuration_error,
    handle_get_display_messages_error,
    handle_get_report_error,
    handle_get_transaction_status_error,
    handle_remote_start_transaction_error,
    handle_remote_stop_transaction_error,
    handle_request_start_transaction_error,
    handle_request_stop_transaction_error,
    handle_reset_error,
    handle_set_display_message_error,
    handle_set_monitoring_base_error,
    handle_set_monitoring_level_error,
    handle_set_network_profile_error,
    handle_trigger_message_error,
    handle_unlock_connector_error,
)
from .data_transfer import handle_data_transfer_error
from .firmware import (
    handle_get_composite_schedule_error,
    handle_get_diagnostics_error,
    handle_get_log_error,
    handle_publish_firmware_error,
    handle_unpublish_firmware_error,
    handle_update_firmware_error,
)
from .profiles import (
    handle_clear_charging_profile_error,
    handle_get_charging_profiles_error,
    handle_set_charging_profile_error,
)
from .reservation import handle_cancel_reservation_error, handle_reserve_now_error
from .types import CallErrorContext, CallErrorDetails, CallErrorHandler, CallMetadata


CALL_ERROR_HANDLERS: dict[str, CallErrorHandler] = MappingProxyType({
    "GetCompositeSchedule": handle_get_composite_schedule_error,
    "ChangeConfiguration": handle_change_configuration_error,
    "GetLog": handle_get_log_error,
    "DataTransfer": handle_data_transfer_error,
    "ClearCache": handle_clear_cache_error,
    "GetConfiguration": handle_get_configuration_error,
    "TriggerMessage": handle_trigger_message_error,
    "UpdateFirmware": handle_update_firmware_error,
    "PublishFirmware": handle_publish_firmware_error,
    "UnpublishFirmware": handle_unpublish_firmware_error,
    "ReserveNow": handle_reserve_now_error,
    "CancelReservation": handle_cancel_reservation_error,
    "RemoteStartTransaction": handle_remote_start_transaction_error,
    "RemoteStopTransaction": handle_remote_stop_transaction_error,
    "GetDiagnostics": handle_get_diagnostics_error,
    "RequestStartTransaction": handle_request_start_transaction_error,
    "RequestStopTransaction": handle_request_stop_transaction_error,
    "GetTransactionStatus": handle_get_transaction_status_error,
    "Reset": handle_reset_error,
    "ChangeAvailability": handle_change_availability_error,
    "UnlockConnector": handle_unlock_connector_error,
    "SetChargingProfile": handle_set_charging_profile_error,
    "ClearChargingProfile": handle_clear_charging_profile_error,
    "ClearDisplayMessage": handle_clear_display_message_error,
    "CustomerInformation": handle_customer_information_error,
    "GetBaseReport": handle_get_base_report_error,
    "GetChargingProfiles": handle_get_charging_profiles_error,
    "GetDisplayMessages": handle_get_display_messages_error,
    "GetReport": handle_get_report_error,
    "SetDisplayMessage": handle_set_display_message_error,
    "SetMonitoringBase": handle_set_monitoring_base_error,
    "SetMonitoringLevel": handle_set_monitoring_level_error,
    "SetNetworkProfile": handle_set_network_profile_error,
    "InstallCertificate": handle_install_certificate_error,
    "DeleteCertificate": handle_delete_certificate_error,
    "CertificateSigned": handle_certificate_signed_error,
    "GetInstalledCertificateIds": handle_get_installed_certificate_ids_error,
})


logger = logging.getLogger(__name__)


async def dispatch_call_error(
    consumer: CallErrorContext,
    action: str | None,
    message_id: str,
    metadata: CallMetadata,
    error_code: str | None,
    description: str | None,
    details: CallErrorDetails | None,
    log_key: str,
) -> bool:
    """Dispatch a call error to the action-specific handler."""
    if not action:
        return False
    handler = CALL_ERROR_HANDLERS.get(action)
    if not handler:
        return False
    try:
        return await handler(consumer, message_id, metadata, error_code, description, details, log_key)
    except Exception:
        logger.exception("Call error handler failed for action %s", action)
        return False

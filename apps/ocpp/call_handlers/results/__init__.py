"""Call result handler registry and entry points."""
from __future__ import annotations

from ..types import CallResultContext, CallResultHandler
from .availability import (
    handle_change_availability_result,
    handle_unlock_connector_result,
)
from .certificates import (
    handle_certificate_signed_result,
    handle_delete_certificate_result,
    handle_get_installed_certificate_ids_result,
    handle_install_certificate_result,
)
from .charging_profiles import (
    handle_clear_charging_profile_result,
    handle_get_charging_profiles_result,
    handle_set_charging_profile_result,
)
from .configuration import (
    handle_change_configuration_result,
    handle_get_configuration_result,
)
from .customer_information import handle_customer_information_result
from .data_transfer import handle_data_transfer_result
from .diagnostics import handle_get_diagnostics_result
from .display import (
    handle_clear_display_message_result,
    handle_get_display_messages_result,
    handle_set_display_message_result,
)
from .firmware import (
    handle_publish_firmware_result,
    handle_unpublish_firmware_result,
    handle_update_firmware_result,
)
from .local_list import (
    handle_clear_cache_result,
    handle_get_local_list_version_result,
    handle_send_local_list_result,
)
from .logs import handle_get_log_result
from .monitoring import (
    handle_clear_variable_monitoring_result,
    handle_get_monitoring_report_result,
    handle_get_variables_result,
    handle_set_monitoring_base_result,
    handle_set_monitoring_level_result,
    handle_set_variable_monitoring_result,
    handle_set_variables_result,
)
from .network_profile import handle_set_network_profile_result
from .reporting import (
    handle_get_base_report_result,
    handle_get_report_result,
)
from .reservations import (
    handle_cancel_reservation_result,
    handle_reserve_now_result,
)
from .reset import handle_reset_result
from .schedule import handle_get_composite_schedule_result
from .transactions import (
    handle_get_transaction_status_result,
    handle_remote_start_transaction_result,
    handle_remote_stop_transaction_result,
    handle_request_start_transaction_result,
    handle_request_stop_transaction_result,
)
from .trigger import handle_trigger_message_result


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
    """Dispatch call result payloads to the correct handler."""
    if not action:
        return False
    handler = CALL_RESULT_HANDLERS.get(action)
    if not handler:
        return False
    return await handler(consumer, message_id, metadata, payload_data, log_key)


__all__ = [
    "CALL_RESULT_HANDLERS",
    "dispatch_call_result",
    "handle_change_configuration_result",
    "handle_data_transfer_result",
    "handle_get_composite_schedule_result",
    "handle_get_log_result",
    "handle_send_local_list_result",
    "handle_get_local_list_version_result",
    "handle_clear_cache_result",
    "handle_update_firmware_result",
    "handle_publish_firmware_result",
    "handle_unpublish_firmware_result",
    "handle_get_configuration_result",
    "handle_trigger_message_result",
    "handle_reserve_now_result",
    "handle_cancel_reservation_result",
    "handle_remote_start_transaction_result",
    "handle_remote_stop_transaction_result",
    "handle_get_diagnostics_result",
    "handle_request_start_transaction_result",
    "handle_request_stop_transaction_result",
    "handle_get_transaction_status_result",
    "handle_reset_result",
    "handle_change_availability_result",
    "handle_unlock_connector_result",
    "handle_set_charging_profile_result",
    "handle_clear_charging_profile_result",
    "handle_clear_display_message_result",
    "handle_customer_information_result",
    "handle_get_base_report_result",
    "handle_get_charging_profiles_result",
    "handle_get_display_messages_result",
    "handle_get_report_result",
    "handle_set_display_message_result",
    "handle_set_monitoring_base_result",
    "handle_set_monitoring_level_result",
    "handle_set_network_profile_result",
    "handle_install_certificate_result",
    "handle_delete_certificate_result",
    "handle_certificate_signed_result",
    "handle_get_installed_certificate_ids_result",
    "handle_get_variables_result",
    "handle_set_variables_result",
    "handle_set_variable_monitoring_result",
    "handle_clear_variable_monitoring_result",
    "handle_get_monitoring_report_result",
]

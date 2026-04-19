"""Central registry for OCPP call result handlers."""

from __future__ import annotations

from .common import ContextHandler
from .authorization import clear_cache, get_local_list_version, send_local_list
from .certificates import (
    certificate_signed,
    delete_certificate,
    get_installed_certificate_ids,
    install_certificate,
)
from .configuration import (
    change_availability,
    change_configuration,
    get_configuration,
    reset,
    set_network_profile,
    trigger_message,
    unlock_connector,
)
from .diagnostics import get_diagnostics, get_log
from .firmware import data_transfer, publish_firmware, unpublish_firmware, update_firmware
from .profiles import (
    clear_charging_profile,
    clear_display_message,
    clear_variable_monitoring,
    customer_information,
    get_base_report,
    get_charging_profiles,
    get_composite_schedule,
    get_display_messages,
    get_monitoring_report,
    get_report,
    get_variables,
    set_charging_profile,
    set_display_message,
    set_monitoring_base,
    set_monitoring_level,
    set_variable_monitoring,
    set_variables,
)
from .transactions import (
    cancel_reservation,
    get_transaction_status,
    remote_start_transaction,
    remote_stop_transaction,
    request_start_transaction,
    request_stop_transaction,
    reserve_now,
)

CALL_RESULT_HANDLER_REGISTRY: dict[str, ContextHandler] = {
    "CancelReservation": cancel_reservation,
    "CertificateSigned": certificate_signed,
    "ChangeAvailability": change_availability,
    "ChangeConfiguration": change_configuration,
    "ClearCache": clear_cache,
    "ClearChargingProfile": clear_charging_profile,
    "ClearDisplayMessage": clear_display_message,
    "ClearVariableMonitoring": clear_variable_monitoring,
    "CustomerInformation": customer_information,
    "DataTransfer": data_transfer,
    "DeleteCertificate": delete_certificate,
    "GetBaseReport": get_base_report,
    "GetChargingProfiles": get_charging_profiles,
    "GetCompositeSchedule": get_composite_schedule,
    "GetConfiguration": get_configuration,
    "GetDiagnostics": get_diagnostics,
    "GetDisplayMessages": get_display_messages,
    "GetInstalledCertificateIds": get_installed_certificate_ids,
    "GetLocalListVersion": get_local_list_version,
    "GetLog": get_log,
    "GetMonitoringReport": get_monitoring_report,
    "GetReport": get_report,
    "GetTransactionStatus": get_transaction_status,
    "GetVariables": get_variables,
    "InstallCertificate": install_certificate,
    "PublishFirmware": publish_firmware,
    "RemoteStartTransaction": remote_start_transaction,
    "RemoteStopTransaction": remote_stop_transaction,
    "RequestStartTransaction": request_start_transaction,
    "RequestStopTransaction": request_stop_transaction,
    "ReserveNow": reserve_now,
    "Reset": reset,
    "SendLocalList": send_local_list,
    "SetChargingProfile": set_charging_profile,
    "SetDisplayMessage": set_display_message,
    "SetMonitoringBase": set_monitoring_base,
    "SetMonitoringLevel": set_monitoring_level,
    "SetNetworkProfile": set_network_profile,
    "SetVariableMonitoring": set_variable_monitoring,
    "SetVariables": set_variables,
    "TriggerMessage": trigger_message,
    "UnlockConnector": unlock_connector,
    "UnpublishFirmware": unpublish_firmware,
    "UpdateFirmware": update_firmware,
}

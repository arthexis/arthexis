"""Central registry for OCPP call result handlers."""

from __future__ import annotations

from . import authorization, certificates, configuration, diagnostics, firmware, profiles, transactions
from .common import ContextHandler, LegacyHandler, legacy_adapter

CALL_RESULT_HANDLER_REGISTRY: dict[str, ContextHandler] = {
    "ChangeConfiguration": configuration.change_configuration,
    "DataTransfer": firmware.data_transfer,
    "GetCompositeSchedule": profiles.get_composite_schedule,
    "GetLog": diagnostics.get_log,
    "SendLocalList": authorization.send_local_list,
    "GetLocalListVersion": authorization.get_local_list_version,
    "ClearCache": authorization.clear_cache,
    "UpdateFirmware": firmware.update_firmware,
    "PublishFirmware": firmware.publish_firmware,
    "UnpublishFirmware": firmware.unpublish_firmware,
    "GetConfiguration": configuration.get_configuration,
    "TriggerMessage": configuration.trigger_message,
    "ReserveNow": transactions.reserve_now,
    "CancelReservation": transactions.cancel_reservation,
    "RemoteStartTransaction": transactions.remote_start_transaction,
    "RemoteStopTransaction": transactions.remote_stop_transaction,
    "GetDiagnostics": diagnostics.get_diagnostics,
    "RequestStartTransaction": transactions.request_start_transaction,
    "RequestStopTransaction": transactions.request_stop_transaction,
    "GetTransactionStatus": transactions.get_transaction_status,
    "Reset": configuration.reset,
    "ChangeAvailability": configuration.change_availability,
    "UnlockConnector": configuration.unlock_connector,
    "SetChargingProfile": profiles.set_charging_profile,
    "ClearChargingProfile": profiles.clear_charging_profile,
    "ClearDisplayMessage": profiles.clear_display_message,
    "CustomerInformation": profiles.customer_information,
    "GetBaseReport": profiles.get_base_report,
    "GetChargingProfiles": profiles.get_charging_profiles,
    "GetDisplayMessages": profiles.get_display_messages,
    "GetReport": profiles.get_report,
    "SetDisplayMessage": profiles.set_display_message,
    "SetMonitoringBase": profiles.set_monitoring_base,
    "SetMonitoringLevel": profiles.set_monitoring_level,
    "SetNetworkProfile": configuration.set_network_profile,
    "InstallCertificate": certificates.install_certificate,
    "DeleteCertificate": certificates.delete_certificate,
    "CertificateSigned": certificates.certificate_signed,
    "GetInstalledCertificateIds": certificates.get_installed_certificate_ids,
    "GetVariables": profiles.get_variables,
    "SetVariables": profiles.set_variables,
    "SetVariableMonitoring": profiles.set_variable_monitoring,
    "ClearVariableMonitoring": profiles.clear_variable_monitoring,
    "GetMonitoringReport": profiles.get_monitoring_report,
}


def build_legacy_registry() -> dict[str, LegacyHandler]:
    """Build a registry compatible with the historical handler signature."""

    return {action: legacy_adapter(handler) for action, handler in CALL_RESULT_HANDLER_REGISTRY.items()}

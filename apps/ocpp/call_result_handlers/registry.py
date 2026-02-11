"""Central registry for OCPP call result handlers."""

from __future__ import annotations

from . import authorization, certificates, configuration, diagnostics, firmware, profiles, transactions
from .common import ContextHandler, LegacyHandler, legacy_adapter

CALL_RESULT_HANDLER_REGISTRY: dict[str, ContextHandler] = {
    "CancelReservation": transactions.cancel_reservation,
    "CertificateSigned": certificates.certificate_signed,
    "ChangeAvailability": configuration.change_availability,
    "ChangeConfiguration": configuration.change_configuration,
    "ClearCache": authorization.clear_cache,
    "ClearChargingProfile": profiles.clear_charging_profile,
    "ClearDisplayMessage": profiles.clear_display_message,
    "ClearVariableMonitoring": profiles.clear_variable_monitoring,
    "CustomerInformation": profiles.customer_information,
    "DataTransfer": firmware.data_transfer,
    "DeleteCertificate": certificates.delete_certificate,
    "GetBaseReport": profiles.get_base_report,
    "GetChargingProfiles": profiles.get_charging_profiles,
    "GetCompositeSchedule": profiles.get_composite_schedule,
    "GetConfiguration": configuration.get_configuration,
    "GetDiagnostics": diagnostics.get_diagnostics,
    "GetDisplayMessages": profiles.get_display_messages,
    "GetInstalledCertificateIds": certificates.get_installed_certificate_ids,
    "GetLocalListVersion": authorization.get_local_list_version,
    "GetLog": diagnostics.get_log,
    "GetMonitoringReport": profiles.get_monitoring_report,
    "GetReport": profiles.get_report,
    "GetTransactionStatus": transactions.get_transaction_status,
    "GetVariables": profiles.get_variables,
    "InstallCertificate": certificates.install_certificate,
    "PublishFirmware": firmware.publish_firmware,
    "RemoteStartTransaction": transactions.remote_start_transaction,
    "RemoteStopTransaction": transactions.remote_stop_transaction,
    "RequestStartTransaction": transactions.request_start_transaction,
    "RequestStopTransaction": transactions.request_stop_transaction,
    "ReserveNow": transactions.reserve_now,
    "Reset": configuration.reset,
    "SendLocalList": authorization.send_local_list,
    "SetChargingProfile": profiles.set_charging_profile,
    "SetDisplayMessage": profiles.set_display_message,
    "SetMonitoringBase": profiles.set_monitoring_base,
    "SetMonitoringLevel": profiles.set_monitoring_level,
    "SetNetworkProfile": configuration.set_network_profile,
    "SetVariableMonitoring": profiles.set_variable_monitoring,
    "SetVariables": profiles.set_variables,
    "TriggerMessage": configuration.trigger_message,
    "UnlockConnector": configuration.unlock_connector,
    "UnpublishFirmware": firmware.unpublish_firmware,
    "UpdateFirmware": firmware.update_firmware,
}


def build_legacy_registry() -> dict[str, LegacyHandler]:
    """Build a registry compatible with the historical handler signature."""

    return {action: legacy_adapter(handler) for action, handler in CALL_RESULT_HANDLER_REGISTRY.items()}

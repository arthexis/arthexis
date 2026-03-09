"""Regression tests for call error handler dispatch wiring."""

from __future__ import annotations

import pytest
from channels.db import database_sync_to_async
from django.utils import timezone

from apps.maps.models import Location
from apps.ocpp import call_error_handlers, store
from apps.ocpp.call_error_handlers import certificates, configuration, data_transfer, firmware, profiles, reservation
from apps.ocpp.consumers import CSMSConsumer
from apps.ocpp.models import (
    CPReservation,
    CPFirmware,
    CPFirmwareDeployment,
    CertificateOperation,
    Charger,
    ChargingProfile,
    DataTransferMessage,
    InstalledCertificate,
)


def test_dispatch_registry_preserves_action_mapping() -> None:
    expected = {
        "GetCompositeSchedule": firmware.handle_get_composite_schedule_error,
        "ChangeConfiguration": configuration.handle_change_configuration_error,
        "GetLog": firmware.handle_get_log_error,
        "DataTransfer": data_transfer.handle_data_transfer_error,
        "ClearCache": configuration.handle_clear_cache_error,
        "GetConfiguration": configuration.handle_get_configuration_error,
        "TriggerMessage": configuration.handle_trigger_message_error,
        "UpdateFirmware": firmware.handle_update_firmware_error,
        "PublishFirmware": firmware.handle_publish_firmware_error,
        "UnpublishFirmware": firmware.handle_unpublish_firmware_error,
        "ReserveNow": reservation.handle_reserve_now_error,
        "CancelReservation": reservation.handle_cancel_reservation_error,
        "RemoteStartTransaction": configuration.handle_remote_start_transaction_error,
        "RemoteStopTransaction": configuration.handle_remote_stop_transaction_error,
        "GetDiagnostics": firmware.handle_get_diagnostics_error,
        "RequestStartTransaction": configuration.handle_request_start_transaction_error,
        "RequestStopTransaction": configuration.handle_request_stop_transaction_error,
        "GetTransactionStatus": configuration.handle_get_transaction_status_error,
        "Reset": configuration.handle_reset_error,
        "ChangeAvailability": configuration.handle_change_availability_error,
        "UnlockConnector": configuration.handle_unlock_connector_error,
        "SetChargingProfile": profiles.handle_set_charging_profile_error,
        "ClearChargingProfile": profiles.handle_clear_charging_profile_error,
        "ClearDisplayMessage": configuration.handle_clear_display_message_error,
        "CustomerInformation": configuration.handle_customer_information_error,
        "GetBaseReport": configuration.handle_get_base_report_error,
        "GetChargingProfiles": profiles.handle_get_charging_profiles_error,
        "GetDisplayMessages": configuration.handle_get_display_messages_error,
        "GetReport": configuration.handle_get_report_error,
        "SetDisplayMessage": configuration.handle_set_display_message_error,
        "SetMonitoringBase": configuration.handle_set_monitoring_base_error,
        "SetMonitoringLevel": configuration.handle_set_monitoring_level_error,
        "SetNetworkProfile": configuration.handle_set_network_profile_error,
        "InstallCertificate": certificates.handle_install_certificate_error,
        "DeleteCertificate": certificates.handle_delete_certificate_error,
        "CertificateSigned": certificates.handle_certificate_signed_error,
        "GetInstalledCertificateIds": certificates.handle_get_installed_certificate_ids_error,
    }
    assert call_error_handlers.CALL_ERROR_HANDLERS == expected


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_dispatch_firmware_domain() -> None:
    firmware_obj = await database_sync_to_async(CPFirmware.objects.create)(name="FW", payload_json={})
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="ERR-FW-1")
    deployment = await database_sync_to_async(CPFirmwareDeployment.objects.create)(
        firmware=firmware_obj,
        charger=charger,
        status="Pending",
        status_timestamp=timezone.now(),
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = charger.charger_id
    result = await call_error_handlers.dispatch_call_error(
        consumer,
        "UpdateFirmware",
        "m-fw",
        {"deployment_pk": deployment.pk},
        "InternalError",
        "failed",
        {"reason": "regression"},
        charger.charger_id,
    )
    deployment = await database_sync_to_async(CPFirmwareDeployment.objects.get)(pk=deployment.pk)
    assert result is True
    assert deployment.status == "Error"
    assert store.has_pending_result("m-fw")


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_dispatch_configuration_domain() -> None:
    """Regression: UnlockConnector call errors should update availability state."""

    charger = await database_sync_to_async(Charger.objects.create)(charger_id="ERR-CFG-1", connector_id=1)
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger = charger
    consumer.aggregate_charger = None
    result = await call_error_handlers.dispatch_call_error(
        consumer,
        "UnlockConnector",
        "m-cfg",
        {"connector_id": 1, "requested_at": timezone.now()},
        "InternalError",
        "unlock failed",
        {"detail": "boom"},
        charger.charger_id,
    )
    refreshed = await database_sync_to_async(Charger.objects.get)(pk=charger.pk)
    assert result is True
    assert refreshed.availability_request_status == "Rejected"
    assert store.has_pending_result("m-cfg")


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_dispatch_reservation_domain() -> None:
    location = await database_sync_to_async(Location.objects.create)(name="ERR-RES")
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="ERR-RES-1", connector_id=1, location=location)
    item = await database_sync_to_async(CPReservation.objects.create)(
        location=location,
        connector=charger,
        start_time=timezone.now(),
        duration_minutes=30,
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    result = await call_error_handlers.dispatch_call_error(
        consumer,
        "ReserveNow",
        "m-res",
        {"reservation_pk": item.pk},
        "InternalError",
        "reserve failed",
        {"detail": "conflict"},
        charger.charger_id,
    )
    item = await database_sync_to_async(CPReservation.objects.get)(pk=item.pk)
    assert result is True
    assert item.evcs_confirmed is False
    assert item.evcs_error
    assert store.has_pending_result("m-res")


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_dispatch_certificates_domain() -> None:
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="ERR-CERT-1")
    op = await database_sync_to_async(CertificateOperation.objects.create)(
        charger=charger,
        action=CertificateOperation.ACTION_INSTALL,
    )
    cert = await database_sync_to_async(InstalledCertificate.objects.create)(charger=charger)
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    result = await call_error_handlers.dispatch_call_error(
        consumer,
        "InstallCertificate",
        "m-cert",
        {"operation_pk": op.pk, "installed_certificate_pk": cert.pk},
        "InternalError",
        "cert failed",
        {"detail": "bad cert"},
        charger.charger_id,
    )
    op = await database_sync_to_async(CertificateOperation.objects.get)(pk=op.pk)
    cert = await database_sync_to_async(InstalledCertificate.objects.get)(pk=cert.pk)
    assert result is True
    assert op.status == CertificateOperation.STATUS_ERROR
    assert cert.status == InstalledCertificate.STATUS_ERROR
    assert store.has_pending_result("m-cert")


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_dispatch_profiles_domain() -> None:
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="ERR-PROF-1")
    await database_sync_to_async(ChargingProfile.objects.create)(
        charger=charger,
        connector_id=1,
        charging_profile_id=44,
        stack_level=1,
        purpose=ChargingProfile.Purpose.CHARGE_POINT_MAX_PROFILE,
        kind=ChargingProfile.Kind.ABSOLUTE,
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    result = await call_error_handlers.dispatch_call_error(
        consumer,
        "ClearChargingProfile",
        "m-prof",
        {"charging_profile_id": 44, "charger_id": charger.charger_id},
        "InternalError",
        "clear failed",
        {"detail": "missing"},
        charger.charger_id,
    )
    profile = await database_sync_to_async(ChargingProfile.objects.get)(charger=charger, charging_profile_id=44)
    assert result is True
    assert profile.last_status == "InternalError"
    assert store.has_pending_result("m-prof")


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_dispatch_data_transfer_domain() -> None:
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="ERR-DATA-1")
    message = await database_sync_to_async(DataTransferMessage.objects.create)(
        charger=charger,
        direction=DataTransferMessage.DIRECTION_CSMS_TO_CP,
        ocpp_message_id="ocpp-1",
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    result = await call_error_handlers.dispatch_call_error(
        consumer,
        "DataTransfer",
        "m-data",
        {"message_pk": message.pk},
        "InternalError",
        "xfer failed",
        {"detail": "timeout"},
        charger.charger_id,
    )
    message = await database_sync_to_async(DataTransferMessage.objects.get)(pk=message.pk)
    assert result is True
    assert message.status == "InternalError"
    assert store.has_pending_result("m-data")

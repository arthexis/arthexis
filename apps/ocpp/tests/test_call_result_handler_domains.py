from __future__ import annotations

import pytest
from channels.db import database_sync_to_async
from django.utils import timezone

from apps.ocpp.consumers import CSMSConsumer
from apps.ocpp import store
from apps.ocpp.call_result_handlers import (
    authorization,
    certificates,
    configuration,
    diagnostics,
    firmware,
    profiles,
    transactions,
)
from apps.maps.models import Location

from apps.ocpp.models import (
    CPReservation,
    Charger,
    ChargerLogRequest,
    ChargingProfile,
    CertificateOperation,
    CPFirmware,
    CPFirmwareDeployment,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_configuration_domain_tracks_status_and_resilience():
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger_id = "CFG-1"
    consumer.store_key = "CFG-1"

    ok = await configuration.handle_change_availability_result(
        consumer,
        "cfg-msg",
        {"requested_at": timezone.now(), "connector_id": 1, "availability_type": "Inoperative"},
        {"status": "Accepted"},
        consumer.store_key,
    )
    malformed = await configuration.handle_change_configuration_result(
        consumer,
        "cfg-msg-2",
        {},
        {},
        consumer.store_key,
    )

    assert ok is True
    assert malformed is True


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_firmware_domain_updates_deployment():
    firmware_obj = await database_sync_to_async(CPFirmware.objects.create)(name="FW", payload_json={})
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="FW-1")
    deployment = await database_sync_to_async(CPFirmwareDeployment.objects.create)(
        firmware=firmware_obj,
        charger=charger,
        status="Pending",
        status_timestamp=timezone.now(),
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger_id = charger.charger_id
    consumer.store_key = charger.charger_id

    result = await firmware.handle_update_firmware_result(
        consumer,
        "fw-msg",
        {"deployment_pk": deployment.pk},
        {"status": "Rejected"},
        consumer.store_key,
    )

    assert result is True
    deployment = await database_sync_to_async(CPFirmwareDeployment.objects.get)(pk=deployment.pk)
    assert deployment.status == "Rejected"


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_transactions_domain_updates_reservation_and_status_mapping():
    location = await database_sync_to_async(Location.objects.create)(name="Depot")
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="TRX-1", connector_id=1, location=location)
    reservation = await database_sync_to_async(CPReservation.objects.create)(
        location=location,
        connector=charger,
        start_time=timezone.now(),
        duration_minutes=30,
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger_id = charger.charger_id
    consumer.store_key = charger.charger_id

    result = await transactions.handle_reserve_now_result(
        consumer,
        "trx-msg",
        {"reservation_pk": reservation.pk},
        {"status": "Accepted"},
        consumer.store_key,
    )

    assert result is True
    reservation = await database_sync_to_async(CPReservation.objects.get)(pk=reservation.pk)
    assert reservation.evcs_confirmed is True


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_authorization_domain_handles_unknown_payloads():
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger_id = "AUTH-1"
    consumer.store_key = "AUTH-1"
    consumer.charger = None
    consumer.aggregate_charger = None

    result = await authorization.handle_get_local_list_version_result(
        consumer,
        "auth-msg",
        {},
        {"listVersion": "x"},
        consumer.store_key,
    )

    assert result is True


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_profiles_domain_updates_profile_and_ignores_malformed_variable_payload():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="PROF-1")
    profile_obj = await database_sync_to_async(ChargingProfile.objects.create)(
        charger=charger,
        connector_id=1,
        charging_profile_id=77,
        stack_level=1,
        purpose=ChargingProfile.Purpose.CHARGE_POINT_MAX_PROFILE,
        kind=ChargingProfile.Kind.ABSOLUTE,
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger_id = charger.charger_id
    consumer.store_key = charger.charger_id

    ok = await profiles.handle_clear_charging_profile_result(
        consumer,
        "prof-msg",
        {"charging_profile_id": profile_obj.charging_profile_id, "charger_id": charger.charger_id},
        {"status": "Accepted", "statusInfo": {"detail": "ok"}},
        consumer.store_key,
    )
    malformed = await profiles.handle_get_variables_result(
        consumer,
        "prof-msg-2",
        {"charger_id": charger.charger_id},
        {"getVariableResult": "bad-type"},
        consumer.store_key,
    )

    assert ok is True
    assert malformed is True


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_certificates_domain_updates_operation_status():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="CERT-1")
    operation = await database_sync_to_async(CertificateOperation.objects.create)(
        charger=charger,
        action=CertificateOperation.ACTION_SIGNED,
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger_id = charger.charger_id
    consumer.store_key = charger.charger_id

    result = await certificates.handle_certificate_signed_result(
        consumer,
        "cert-msg",
        {"operation_pk": operation.pk},
        {"status": "Rejected", "statusInfo": {"reason": "invalid"}},
        consumer.store_key,
    )

    assert result is True
    operation = await database_sync_to_async(CertificateOperation.objects.get)(pk=operation.pk)
    assert operation.status == CertificateOperation.STATUS_REJECTED


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_diagnostics_domain_updates_log_request_and_diagnostics_metadata():
    charger = await database_sync_to_async(Charger.objects.create)(charger_id="DIA-1")
    request = await database_sync_to_async(ChargerLogRequest.objects.create)(
        charger=charger,
        status="Requested",
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger_id = charger.charger_id
    consumer.store_key = charger.charger_id

    log_result = await diagnostics.handle_get_log_result(
        consumer,
        "diag-msg-1",
        {"log_request_pk": request.pk},
        {"status": "Uploaded", "filename": "logs.txt"},
        consumer.store_key,
    )
    diag_result = await diagnostics.handle_get_diagnostics_result(
        consumer,
        "diag-msg-2",
        {"charger_id": charger.charger_id},
        {"status": "Accepted", "fileName": "diag.zip"},
        consumer.store_key,
    )

    assert log_result is True
    assert diag_result is True
    refreshed = await database_sync_to_async(ChargerLogRequest.objects.get)(pk=request.pk)
    assert refreshed.status == "Uploaded"

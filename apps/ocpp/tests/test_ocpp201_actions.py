import json
from pathlib import Path

import pytest

from apps.ocpp import store
from apps.ocpp.consumers.csms.consumer import CSMSConsumer
from apps.ocpp.consumers.csms.dispatch import build_action_registry
from apps.ocpp.management import coverage_ocpp201_impl, coverage_ocpp21_impl
from apps.ocpp.management.coverage_ocpp21_impl import run_coverage_ocpp21
from apps.ocpp.management.coverage_ocpp201_impl import _collect_real_decorated_actions
from apps.ocpp.models import (
    CertificateOperation,
    Charger,
    CPFirmware,
    CPFirmwareDeployment,
    InstalledCertificate,
)
from apps.ocpp.tasks import request_charge_point_log
from apps.ocpp.views import actions
from apps.ocpp.views.actions import charging_profiles
from apps.ocpp.views.common import ActionCall, ActionContext
from apps.protocols.models import ProtocolCall as ProtocolCallModel


class DummyWebSocket:
    def __init__(self):
        self.sent: list[str] = []
        self.ocpp_version = "ocpp2.0.1"

    async def send(self, message: str) -> None:  # pragma: no cover - exercised via async_to_sync
        self.sent.append(message)


@pytest.fixture
def ws() -> DummyWebSocket:
    return DummyWebSocket()


@pytest.fixture(autouse=True)
def reset_store_state(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    session_dir = log_dir / "sessions"
    lock_dir = tmp_path / "locks"
    session_dir.mkdir(parents=True, exist_ok=True)
    lock_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(store, "LOG_DIR", log_dir)
    monkeypatch.setattr(store, "SESSION_DIR", session_dir)
    monkeypatch.setattr(store, "LOCK_DIR", lock_dir)
    monkeypatch.setattr(store, "SESSION_LOCK", lock_dir / "charging.lck")

    def _clear_state() -> None:
        store.connections.clear()
        store.ip_connections.clear()
        store.logs["charger"].clear()
        store.logs["simulator"].clear()
        store.log_names["charger"].clear()
        store.log_names["simulator"].clear()
        store.pending_calls.clear()
        store._pending_call_events.clear()
        store._pending_call_results.clear()
        for handle in store._pending_call_handles.values():
            store._cancel_timer_handle(handle)
        store._pending_call_handles.clear()
        store.history.clear()
        store.triggered_followups.clear()
        store.monitoring_report_requests.clear()
        store.transaction_requests.clear()
        store._transaction_requests_by_connector.clear()
        store._transaction_requests_by_transaction.clear()

    _clear_state()
    yield
    _clear_state()


def test_unlock_connector_supports_ocpp201(ws):
    log_key = store.identity_key("CID", 2)
    context = ActionContext("CID", 2, charger=None, ws=ws, log_key=log_key)
    result = actions._handle_unlock_connector(context, {})

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "UnlockConnector"
    message_id = message[1]
    assert message_id in store.pending_calls
    assert store.pending_calls[message_id]["log_key"] == log_key
    assert message_id in store._pending_call_handles


def test_send_local_list_supports_ocpp201(ws):
    charger = type("ChargerStub", (), {"local_auth_list_version": 4})
    log_key = store.identity_key("CID", None)
    context = ActionContext("CID", None, charger=charger, ws=ws, log_key=log_key)
    result = actions._handle_send_local_list(
        context,
        {"localAuthorizationList": [{"idTag": "ABC"}]},
    )

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "SendLocalList"
    message_id = message[1]
    assert message_id in store.pending_calls
    assert store.pending_calls[message_id]["list_version"] == 5
    assert message_id in store._pending_call_handles


def test_set_charging_profile_supports_ocpp201(monkeypatch, ws):
    class ProfileStub:
        connector_id = 1
        charging_profile_id = 7

        def as_set_charging_profile_request(self, *, connector_id=None, schedule_payload=None):
            return {
                "connectorId": connector_id,
                "csChargingProfiles": {"chargingProfileId": self.charging_profile_id},
            }

    profile = ProfileStub()

    class QueryStub:
        def select_related(self, *_args, **_kwargs):
            return self

        def filter(self, **_kwargs):
            return self

        def first(self):
            return profile

    monkeypatch.setattr(
        charging_profiles, "ChargingProfile", type("CPModel", (), {"objects": QueryStub()})
    )

    log_key = store.identity_key("CID", 1)
    context = ActionContext("CID", 1, charger=None, ws=ws, log_key=log_key)
    result = actions._handle_set_charging_profile(context, {"profileId": profile.charging_profile_id})

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "SetChargingProfile"
    message_id = message[1]
    assert message_id in store.pending_calls
    assert store.pending_calls[message_id]["charging_profile_id"] == profile.charging_profile_id
    assert message_id in store._pending_call_handles


def test_clear_charging_profile_requires_identifier(ws):
    log_key = store.identity_key("CID", 1)
    context = ActionContext("CID", 1, charger=None, ws=ws, log_key=log_key)

    response = actions._handle_clear_charging_profile(context, {})

    assert response.status_code == 400


def test_clear_charging_profile_accepts_criteria_only(ws):
    log_key = store.identity_key("CID", 1)
    context = ActionContext("CID", 1, charger=None, ws=ws, log_key=log_key)

    result = actions._handle_clear_charging_profile(
        context,
        {"chargingProfileCriteria": {"chargingProfilePurpose": "TxProfile"}},
    )

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "ClearChargingProfile"
    payload = message[3]
    assert payload == {
        "chargingProfileCriteria": {"chargingProfilePurpose": "TxProfile"}
    }
    message_id = message[1]
    assert message_id in store.pending_calls
    assert message_id in store._pending_call_handles


def test_clear_charging_profile_registers_pending_call(ws):
    log_key = store.identity_key("CID", 1)
    context = ActionContext("CID", 1, charger=None, ws=ws, log_key=log_key)

    result = actions._handle_clear_charging_profile(
        context,
        {"chargingProfileId": 7, "evseId": 2},
    )

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "ClearChargingProfile"
    payload = message[3]
    assert payload["chargingProfileId"] == 7
    assert payload["evseId"] == 2
    message_id = message[1]
    assert message_id in store.pending_calls
    assert store.pending_calls[message_id]["charging_profile_id"] == 7
    assert store.pending_calls[message_id]["evse_id"] == 2
    assert message_id in store._pending_call_handles


def test_ocpp201_cp_to_csms_calls_resolve_to_handlers():
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    action_registry = build_action_registry(consumer)

    assert action_registry["BootNotification"] == consumer._handle_boot_notification_action
    assert action_registry["Authorize"] == consumer._handle_authorize_action
    assert action_registry["CostUpdated"] == consumer._handle_cost_updated_action
    assert (
        action_registry["ReservationStatusUpdate"]
        == consumer._handle_reservation_status_update_action
    )

    boot_calls = consumer._handle_boot_notification_action.__protocol_calls__
    authorize_calls = consumer._handle_authorize_action.__protocol_calls__
    cost_calls = consumer._handle_cost_updated_action.__protocol_calls__
    reservation_calls = consumer._handle_reservation_status_update_action.__protocol_calls__

    assert ("ocpp201", ProtocolCallModel.CP_TO_CSMS, "BootNotification") in boot_calls
    assert ("ocpp21", ProtocolCallModel.CP_TO_CSMS, "Authorize") in authorize_calls
    assert ("ocpp201", ProtocolCallModel.CP_TO_CSMS, "Authorize") in authorize_calls
    assert ("ocpp201", ProtocolCallModel.CP_TO_CSMS, "CostUpdated") in cost_calls
    assert (
        "ocpp201",
        ProtocolCallModel.CP_TO_CSMS,
        "ReservationStatusUpdate",
    ) in reservation_calls


def test_authorize_protocol_call_registration_supports_ocpp201_and_ocpp21():
    protocol_calls = CSMSConsumer._handle_authorize_action.__protocol_calls__

    assert ("ocpp201", ProtocolCallModel.CP_TO_CSMS, "Authorize") in protocol_calls
    assert ("ocpp21", ProtocolCallModel.CP_TO_CSMS, "Authorize") in protocol_calls


@pytest.mark.anyio
async def test_authorize_translates_id_tag_info_to_id_token_info_for_ocpp2x():
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.ocpp_version = "ocpp2.1"

    class Handler:
        async def handle(self, payload, msg_id, raw, text_data):
            assert payload["idTag"] == "TAG-2X"
            return {"idTagInfo": {"status": "Accepted"}}

    consumer._action_handler = lambda action: Handler()

    result = await consumer._handle_authorize_action(
        {"idToken": {"idToken": "TAG-2X"}},
        "msg-auth-2x",
        "",
        "",
    )

    assert result == {"idTokenInfo": {"status": "Accepted"}}


@pytest.mark.anyio
async def test_authorize_translates_id_tag_info_to_id_token_info_with_extra_fields_for_ocpp2x():
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.ocpp_version = "ocpp2.0.1"

    class Handler:
        async def handle(self, payload, msg_id, raw, text_data):
            assert payload["idTag"] == "TAG-2X-EXTRA"
            return {
                "certificateStatus": "Accepted",
                "idTagInfo": {"status": "Accepted"},
            }

    consumer._action_handler = lambda action: Handler()

    result = await consumer._handle_authorize_action(
        {"idToken": {"idToken": "TAG-2X-EXTRA"}},
        "msg-auth-2x-extra",
        "",
        "",
    )

    assert result == {
        "certificateStatus": "Accepted",
        "idTokenInfo": {"status": "Accepted"},
    }


@pytest.mark.anyio
async def test_authorize_keeps_id_token_info_shape_for_ocpp2x():
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.ocpp_version = "ocpp2.1"

    class Handler:
        async def handle(self, payload, msg_id, raw, text_data):
            assert payload["idTag"] == "TAG-2X-IDTOKEN"
            return {"idTokenInfo": {"status": "Accepted"}}

    consumer._action_handler = lambda action: Handler()

    result = await consumer._handle_authorize_action(
        {"idTag": "TAG-2X-IDTOKEN"},
        "msg-auth-2x-idtoken",
        "",
        "",
    )

    assert result == {"idTokenInfo": {"status": "Accepted"}}

async def test_boot_notification_normalizes_ocpp2x_payload(caplog):
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.ocpp_version = "ocpp2.0.1"

    with caplog.at_level("DEBUG"):
        response = await consumer._handle_boot_notification_action(
            {
                "chargingStation": {"vendorName": "Acme", "model": "Fast-50"},
                "reason": "PowerUp",
            },
            "msg-boot-2x",
            "",
            "",
        )

    assert response["status"] == "Accepted"
    assert response["interval"] == 300
    assert any(
        "BootNotification payload normalized" in record.getMessage()
        and record.payload["chargePointVendor"] == "Acme"
        and record.payload["chargePointModel"] == "Fast-50"
        and record.payload["bootReason"] == "PowerUp"
        for record in caplog.records
    )


@pytest.mark.anyio
async def test_authorize_keeps_id_tag_info_for_ocpp16():
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.ocpp_version = "ocpp1.6"

    class Handler:
        async def handle(self, payload, msg_id, raw, text_data):
            assert payload["idTag"] == "TAG-16"
            return {"idTagInfo": {"status": "Accepted"}}

    consumer._action_handler = lambda action: Handler()

    result = await consumer._handle_authorize_action(
        {"idTag": "TAG-16"},
        "msg-auth-16",
        "",
        "",
    )

    assert result == {"idTagInfo": {"status": "Accepted"}}


def test_firmware_actions_register_ocpp201_and_ocpp21():
    update_calls = actions._handle_update_firmware.__protocol_calls__
    publish_calls = actions._handle_publish_firmware.__protocol_calls__
    unpublish_calls = actions._handle_unpublish_firmware.__protocol_calls__

    assert ("ocpp201", ProtocolCallModel.CSMS_TO_CP, "UpdateFirmware") in update_calls
    assert ("ocpp21", ProtocolCallModel.CSMS_TO_CP, "UpdateFirmware") in update_calls
    assert ("ocpp201", ProtocolCallModel.CSMS_TO_CP, "PublishFirmware") in publish_calls
    assert ("ocpp21", ProtocolCallModel.CSMS_TO_CP, "PublishFirmware") in publish_calls
    assert ("ocpp201", ProtocolCallModel.CSMS_TO_CP, "UnpublishFirmware") in unpublish_calls
    assert ("ocpp21", ProtocolCallModel.CSMS_TO_CP, "UnpublishFirmware") in unpublish_calls


@pytest.mark.django_db
def test_publish_firmware_supports_ocpp201(ws):
    charger = Charger.objects.create(charger_id="FW-CP-1")
    firmware = CPFirmware.objects.create(name="Test Firmware", payload_json={"v": 1})
    log_key = store.identity_key(charger.charger_id, charger.connector_id)
    context = ActionContext(charger.charger_id, charger.connector_id, charger, ws, log_key)

    result = actions._handle_publish_firmware(context, {"firmwareId": firmware.pk})

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "PublishFirmware"
    message_id = message[1]
    payload = message[3]
    assert "location" in payload
    assert payload["requestId"]
    deployment = CPFirmwareDeployment.objects.get(pk=payload["requestId"])
    assert deployment.firmware_id == firmware.pk
    assert message_id in store.pending_calls


@pytest.mark.django_db
def test_unpublish_firmware_supports_ocpp201(ws):
    charger = Charger.objects.create(charger_id="FW-CP-2")
    firmware = CPFirmware.objects.create(name="Test Firmware", payload_json={"v": 2})
    log_key = store.identity_key(charger.charger_id, charger.connector_id)
    context = ActionContext(charger.charger_id, charger.connector_id, charger, ws, log_key)

    result = actions._handle_unpublish_firmware(context, {"firmwareId": firmware.pk})

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "UnpublishFirmware"
    payload = message[3]
    assert payload["checksum"] == firmware.checksum
    message_id = message[1]
    assert message_id in store.pending_calls


@pytest.mark.django_db
def test_get_log_supports_ocpp201(monkeypatch, ws):
    monkeypatch.setattr(Charger, "get_absolute_url", lambda self: "/charger/")
    monkeypatch.setattr(Charger, "_full_url", lambda self: "https://example.com/charger/")

    charger = Charger.objects.create(charger_id="CID-LOG")
    connector_value = charger.connector_id
    store.set_connection(charger.charger_id, connector_value, ws)

    request_pk = request_charge_point_log(charger.pk, log_type="Diagnostics")

    assert request_pk
    message = json.loads(ws.sent[0])
    assert message[2] == "GetLog"
    message_id = message[1]
    assert message_id in store.pending_calls
    assert store.pending_calls[message_id]["log_request_pk"] == request_pk
    assert message_id in store._pending_call_handles

    log_key = store.identity_key(charger.charger_id, connector_value)
    assert log_key in store.logs["charger"]
    assert any("GetLog" in entry for entry in store.logs["charger"][log_key])


@pytest.mark.django_db
def test_install_certificate_registers_pending_call(ws):
    charger = Charger.objects.create(charger_id="CERT-CP-1")
    log_key = store.identity_key(charger.charger_id, charger.connector_id)
    context = ActionContext(charger.charger_id, charger.connector_id, charger, ws, log_key)

    result = actions._handle_install_certificate(
        context,
        {"certificate": "CERTDATA", "certificateType": "V2G"},
    )

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "InstallCertificate"
    message_id = message[1]
    assert message_id in store.pending_calls
    metadata = store.pending_calls[message_id]
    assert CertificateOperation.objects.filter(pk=metadata["operation_pk"]).exists()
    assert InstalledCertificate.objects.filter(pk=metadata["installed_certificate_pk"]).exists()


@pytest.mark.django_db
def test_delete_certificate_registers_pending_call(ws):
    charger = Charger.objects.create(charger_id="CERT-CP-2")
    hash_data = {"hashAlgorithm": "SHA256", "issuerNameHash": "abc"}
    installed = InstalledCertificate.objects.create(
        charger=charger,
        certificate_type="V2G",
        certificate_hash_data=hash_data,
        status=InstalledCertificate.STATUS_INSTALLED,
    )
    log_key = store.identity_key(charger.charger_id, charger.connector_id)
    context = ActionContext(charger.charger_id, charger.connector_id, charger, ws, log_key)

    result = actions._handle_delete_certificate(
        context,
        {"certificateHashData": hash_data},
    )

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "DeleteCertificate"
    message_id = message[1]
    assert message_id in store.pending_calls
    metadata = store.pending_calls[message_id]
    assert metadata["installed_certificate_pk"] == installed.pk
    installed.refresh_from_db()
    assert installed.status == InstalledCertificate.STATUS_DELETE_PENDING


@pytest.mark.django_db
def test_certificate_signed_registers_pending_call(ws):
    charger = Charger.objects.create(charger_id="CERT-CP-3")
    log_key = store.identity_key(charger.charger_id, charger.connector_id)
    context = ActionContext(charger.charger_id, charger.connector_id, charger, ws, log_key)

    result = actions._handle_certificate_signed(
        context,
        {"certificateChain": "CHAIN"},
    )

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "CertificateSigned"
    message_id = message[1]
    assert message_id in store.pending_calls


@pytest.mark.django_db
def test_get_installed_certificate_ids_registers_pending_call(ws):
    charger = Charger.objects.create(charger_id="CERT-CP-4")
    log_key = store.identity_key(charger.charger_id, charger.connector_id)
    context = ActionContext(charger.charger_id, charger.connector_id, charger, ws, log_key)

    result = actions._handle_get_installed_certificate_ids(context, {"certificateType": "V2G"})

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "GetInstalledCertificateIds"
    message_id = message[1]
    assert message_id in store.pending_calls


def test_get_variables_registers_pending_call(ws):
    log_key = store.identity_key("CID", None)
    context = ActionContext("CID", None, charger=None, ws=ws, log_key=log_key)
    result = actions._handle_get_variables(
        context,
        {
            "getVariableData": [
                {"component": {"name": "EVSE"}, "variable": {"name": "Voltage"}}
            ]
        },
    )

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "GetVariables"
    message_id = message[1]
    assert message_id in store.pending_calls
    assert store.pending_calls[message_id]["action"] == "GetVariables"


def test_request_start_transaction_registers_pending_call(ws):
    log_key = store.identity_key("CID", 2)
    context = ActionContext("CID", 2, charger=None, ws=ws, log_key=log_key)
    result = actions._handle_request_start_transaction(
        context,
        {"idToken": "ABC", "remoteStartId": 123, "evseId": 2},
    )

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "RequestStartTransaction"
    payload = message[3]
    assert payload["idToken"]["idToken"] == "ABC"
    assert payload["remoteStartId"] == 123
    assert payload["evseId"] == 2
    message_id = message[1]
    assert message_id in store.pending_calls
    assert message_id in store.transaction_requests
    assert store.transaction_requests[message_id]["status"] == "requested"


def test_request_stop_transaction_registers_pending_call(ws):
    log_key = store.identity_key("CID", 1)
    context = ActionContext("CID", 1, charger=None, ws=ws, log_key=log_key)
    result = actions._handle_request_stop_transaction(
        context,
        {"transactionId": "TX-42"},
    )

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "RequestStopTransaction"
    payload = message[3]
    assert payload["transactionId"] == "TX-42"
    message_id = message[1]
    assert message_id in store.pending_calls
    assert store.transaction_requests[message_id]["transaction_id"] == "TX-42"


def test_get_transaction_status_registers_pending_call(ws):
    log_key = store.identity_key("CID", None)
    context = ActionContext("CID", None, charger=None, ws=ws, log_key=log_key)
    result = actions._handle_get_transaction_status(context, {"transactionId": "TX-99"})

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "GetTransactionStatus"
    payload = message[3]
    assert payload["transactionId"] == "TX-99"
    message_id = message[1]
    assert message_id in store.pending_calls


def test_set_variables_registers_pending_call(ws):
    log_key = store.identity_key("CID", None)
    context = ActionContext("CID", None, charger=None, ws=ws, log_key=log_key)
    result = actions._handle_set_variables(
        context,
        {
            "setVariableData": [
                {
                    "component": {"name": "EVSE"},
                    "variable": {"name": "Voltage"},
                    "attributeValue": "230",
                }
            ]
        },
    )

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "SetVariables"
    message_id = message[1]
    assert message_id in store.pending_calls
    metadata = store.pending_calls[message_id]
    assert metadata["action"] == "SetVariables"
    assert metadata["set_variable_data"][0]["attributeValue"] == "230"


def test_get_monitoring_report_registers_pending_request(ws):
    log_key = store.identity_key("CID", None)
    context = ActionContext("CID", None, charger=None, ws=ws, log_key=log_key)
    result = actions._handle_get_monitoring_report(
        context,
        {"reportBase": "ConfigurationInventory"},
    )

    assert isinstance(result, ActionCall)
    message = json.loads(ws.sent[0])
    assert message[2] == "GetMonitoringReport"
    message_id = message[1]
    assert message_id in store.pending_calls
    request_id = store.pending_calls[message_id]["request_id"]
    assert request_id in store.monitoring_report_requests


def test_collect_real_decorated_actions_excludes_stub_handlers(tmp_path):
    app_dir = tmp_path / "apps" / "ocpp"
    app_dir.mkdir(parents=True)
    (app_dir / "coverage_stubs.py").write_text(
        """
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

@protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "StubFromCoverageModule")
def stub_from_module():
    raise NotImplementedError
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (app_dir / "handlers.py").write_text(
        """
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

@protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "RealCpToCsms")
def real_cp_to_csms():
    return {}

@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "RealCsmsToCp")
def real_csms_to_cp():
    return {}

@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "StubByBody")
def stub_by_body():
    raise NotImplementedError("todo")
""".strip()
        + "\n",
        encoding="utf-8",
    )
    tests_dir = app_dir / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_handlers.py").write_text(
        """
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

@protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "FromTests")
def from_tests():
    return {}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cp_to_csms, csms_to_cp = _collect_real_decorated_actions(Path(app_dir), "ocpp201")

    assert "RealCpToCsms" in cp_to_csms
    assert "RealCsmsToCp" in csms_to_cp
    assert "StubFromCoverageModule" not in cp_to_csms
    assert "FromTests" not in cp_to_csms
    assert "StubByBody" not in csms_to_cp


def test_run_coverage_ocpp21_keeps_shared_ocpp201_handlers(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "apps.ocpp.management.coverage_ocpp21_impl._load_spec",
        lambda: {"cp_to_csms": ["Heartbeat"], "csms_to_cp": []},
    )
    monkeypatch.setattr(
        "apps.ocpp.management.coverage_ocpp21_impl._implemented_cp_to_csms",
        lambda _app_dir: {"Heartbeat"},
    )
    monkeypatch.setattr(
        "apps.ocpp.management.coverage_ocpp21_impl._implemented_csms_to_cp",
        lambda _app_dir: set(),
    )

    def _collect(_app_dir, slug):
        if slug == "ocpp201":
            return {"Heartbeat"}, set()
        return set(), set()

    monkeypatch.setattr(
        "apps.ocpp.management.coverage_ocpp21_impl._collect_real_decorated_actions",
        _collect,
    )

    json_path = tmp_path / "coverage21.json"
    badge_path = tmp_path / "coverage21.svg"
    run_coverage_ocpp21(json_path=str(json_path), badge_path=str(badge_path))
    report = json.loads(json_path.read_text(encoding="utf-8"))

    assert report["implemented"]["cp_to_csms"] == ["Heartbeat"]
    assert report["coverage"]["overall"]["percent"] == 100.0


def test_run_coverage_ocpp201_keeps_decorator_only_cp_to_csms(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "apps.ocpp.management.coverage_ocpp201_impl._load_spec",
        lambda: {"cp_to_csms": ["Heartbeat"], "csms_to_cp": []},
    )
    monkeypatch.setattr(
        "apps.ocpp.management.coverage_ocpp201_impl._implemented_cp_to_csms",
        lambda _app_dir: set(),
    )
    monkeypatch.setattr(
        "apps.ocpp.management.coverage_ocpp201_impl._implemented_csms_to_cp",
        lambda _app_dir: set(),
    )
    monkeypatch.setattr(
        "apps.ocpp.management.coverage_ocpp201_impl._collect_real_decorated_actions",
        lambda _app_dir, _slug: ({"Heartbeat"}, set()),
    )

    json_path = tmp_path / "coverage201.json"
    badge_path = tmp_path / "coverage201.svg"
    coverage_ocpp201_impl.run_coverage_ocpp201(
        json_path=str(json_path),
        badge_path=str(badge_path),
    )
    report = json.loads(json_path.read_text(encoding="utf-8"))

    assert report["implemented"]["cp_to_csms"] == ["Heartbeat"]
    assert report["coverage"]["cp_to_csms"]["supported"] == ["Heartbeat"]


@pytest.mark.parametrize(
    ("protocol_slug", "coverage_path", "load_spec"),
    (
        ("ocpp201", Path("apps/ocpp/coverage201.json"), coverage_ocpp201_impl._load_spec),
        ("ocpp21", Path("apps/ocpp/coverage21.json"), coverage_ocpp21_impl._load_spec),
    ),
)
def test_coverage_artifacts_match_decorator_cp_to_csms_reality(
    protocol_slug,
    coverage_path,
    load_spec,
):
    app_dir = Path(__file__).resolve().parents[1]
    spec_cp_to_csms = set(load_spec()["cp_to_csms"])
    decorated_cp_to_csms, _ = _collect_real_decorated_actions(app_dir, protocol_slug)
    if protocol_slug == "ocpp21":
        decorated_cp_to_csms_201, _ = _collect_real_decorated_actions(app_dir, "ocpp201")
        decorated_cp_to_csms |= decorated_cp_to_csms_201
    expected_supported = sorted(spec_cp_to_csms & decorated_cp_to_csms)

    report = json.loads((app_dir.parent.parent / coverage_path).read_text(encoding="utf-8"))
    assert report["coverage"]["cp_to_csms"]["supported"] == expected_supported


@pytest.mark.parametrize("coverage_path", (Path("apps/ocpp/coverage201.json"), Path("apps/ocpp/coverage21.json")))
def test_coverage_artifacts_include_boot_notification_cp_to_csms(coverage_path):
    app_dir = Path(__file__).resolve().parents[1]
    report = json.loads((app_dir.parent.parent / coverage_path).read_text(encoding="utf-8"))
    assert "BootNotification" in report["implemented"]["cp_to_csms"]
    assert "BootNotification" in report["coverage"]["cp_to_csms"]["supported"]

import asyncio
import json
import base64

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import ChannelsLiveServerTestCase, WebsocketCommunicator
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.ocpp import consumers, store
from apps.ocpp.models import Charger, Simulator
from apps.ocpp.simulator import ChargePointSimulator
from apps.rates.models import RateLimit
from config.asgi import application

pytestmark = pytest.mark.django_db(transaction=True)

CONNECT_TIMEOUT = 5


@pytest.fixture(autouse=True)
def clear_store_state():
    cache.clear()
    store.connections.clear()
    store.ip_connections.clear()
    store.logs["charger"].clear()
    store.log_names["charger"].clear()
    RateLimit.objects.all().delete()
    cache.clear()
    yield
    cache.clear()
    store.connections.clear()
    store.ip_connections.clear()
    store.logs["charger"].clear()
    store.log_names["charger"].clear()
    RateLimit.objects.all().delete()
    cache.clear()


@pytest.fixture(autouse=True)
def isolate_log_dir(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "LOG_DIR", log_dir)


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_charge_point_created_for_new_websocket_path():
    async def run_scenario():
        serial = "CP-UNUSED-PATH"
        path = f"/{serial}"

        exists_before = await database_sync_to_async(
            Charger.objects.filter(charger_id=serial, connector_id=None).exists
        )()
        assert exists_before is False

        communicator = WebsocketCommunicator(application, path)
        connected, _ = await communicator.connect(timeout=CONNECT_TIMEOUT)
        assert connected is True

        boot_notification = [
            2,
            "msg-1",
            "BootNotification",
            {"chargePointModel": "UnitTest", "chargePointVendor": "UnitVendor"},
        ]
        await communicator.send_json_to(boot_notification)
        await communicator.receive_json_from()

        async def fetch_charger():
            for _ in range(20):
                charger = await database_sync_to_async(Charger.objects.filter(
                    charger_id=serial, connector_id=None
                ).first)()
                if charger is not None:
                    return charger
                await asyncio.sleep(0.1)
            return None

        charger = await fetch_charger()
        assert charger is not None, "Expected a charger to be created after websocket connect"
        assert charger.last_path == path

        await communicator.disconnect()

    async_to_sync(run_scenario)()


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_charger_page_reverse_resolves_expected_path():
    cid = "CP-TEST-REVERSE"

    assert reverse("charger-page", args=[cid]) == f"/c/{cid}/"


def test_select_subprotocol_prioritizes_preference_and_defaults():
    consumer = consumers.CSMSConsumer(scope={}, receive=None, send=None)

    cases = [
        (
            (
                [
                    consumers.OCPP_VERSION_16,
                    consumers.OCPP_VERSION_201,
                    consumers.OCPP_VERSION_21,
                ],
                consumers.OCPP_VERSION_21,
            ),
            consumers.OCPP_VERSION_21,
        ),
        (
            (
                [consumers.OCPP_VERSION_21, consumers.OCPP_VERSION_201],
                None,
            ),
            consumers.OCPP_VERSION_21,
        ),
        (([consumers.OCPP_VERSION_16], None), consumers.OCPP_VERSION_16),
        ((["unexpected"], None), None),
    ]

    for (offered, preferred), expected in cases:
        assert consumer._select_subprotocol(offered, preferred) == expected


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
@pytest.mark.parametrize(
    "preferred",
    [consumers.OCPP_VERSION_201, consumers.OCPP_VERSION_21],
)
def test_connect_prefers_stored_ocpp2_without_offered_subprotocol(preferred):
    charger = Charger.objects.create(
        charger_id=f"CP-PREFERRED-{preferred}",
        connector_id=None,
        preferred_ocpp_version=preferred,
    )

    async def run_scenario():
        communicator = WebsocketCommunicator(application, f"/{charger.charger_id}")
        communicator.scope["subprotocols"] = []

        connected, agreed = await communicator.connect(timeout=CONNECT_TIMEOUT)

        assert connected is True
        assert agreed is None

        consumer = store.connections[store.pending_key(charger.charger_id)]
        assert consumer.ocpp_version == preferred

        await communicator.disconnect()

    async_to_sync(run_scenario)()


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_ocpp_websocket_rate_limit_enforced():
    async def run_scenario():
        serial = "CP-RATE-LIMIT"
        path = f"/{serial}"

        first = WebsocketCommunicator(application, path)
        connected, _ = await first.connect(timeout=CONNECT_TIMEOUT)
        assert connected is True

        second = WebsocketCommunicator(application, path)
        connected, _ = await second.connect(timeout=CONNECT_TIMEOUT)
        assert connected is False
        await second.disconnect()

        await first.disconnect()

    RateLimit.objects.create(
        content_type=ContentType.objects.get_for_model(Charger),
        scope_key="ocpp-connect",
        limit=1,
        window_seconds=120,
    )

    cache.clear()

    async_to_sync(run_scenario)()


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_ocpp_websocket_rate_limit_window_expires():
    async def run_scenario():
        first = WebsocketCommunicator(application, "/CP-RATE-WINDOW-1")
        first.scope["client"] = ("8.8.8.8", 1000)
        connected, _ = await first.connect(timeout=CONNECT_TIMEOUT)
        assert connected is True

        second = WebsocketCommunicator(application, "/CP-RATE-WINDOW-2")
        second.scope["client"] = ("8.8.8.8", 1001)
        connected, _ = await second.connect(timeout=CONNECT_TIMEOUT)
        assert connected is False
        await second.disconnect()

        await asyncio.sleep(consumers.OCPP_CONNECT_RATE_LIMIT_WINDOW_SECONDS + 0.1)

        third = WebsocketCommunicator(application, "/CP-RATE-WINDOW-3")
        third.scope["client"] = ("8.8.8.8", 1002)
        connected, _ = await third.connect(timeout=CONNECT_TIMEOUT)
        assert connected is True

        await third.disconnect()
        await first.disconnect()

    cache.clear()

    async_to_sync(run_scenario)()


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_local_ip_bypasses_rate_limit_with_custom_scope_client():
    async def run_scenario():
        serial = "CP-LOCAL-BYPASS"
        path = f"/{serial}"

        throttled = WebsocketCommunicator(application, path)
        throttled.scope["client"] = ("8.8.8.8", 1000)
        connected, _ = await throttled.connect(timeout=CONNECT_TIMEOUT)
        assert connected is False

        local = WebsocketCommunicator(application, path)
        local.scope["client"] = ("127.0.0.1", 1001)
        connected, _ = await local.connect(timeout=CONNECT_TIMEOUT)
        assert connected is True

        await local.disconnect()

    RateLimit.objects.create(
        content_type=ContentType.objects.get_for_model(Charger),
        scope_key="ocpp-connect",
        limit=0,
        window_seconds=120,
    )

    cache.clear()

    async_to_sync(run_scenario)()


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_pending_connection_replaced_on_reconnect():
    async def run_scenario():
        serial = "CP-REPLACE"
        path = f"/{serial}"

        first = WebsocketCommunicator(application, path)
        connected, _ = await first.connect(timeout=CONNECT_TIMEOUT)
        assert connected is True

        existing_consumer = store.connections[store.pending_key(serial)]

        second = WebsocketCommunicator(application, path)
        connected, _ = await second.connect(timeout=CONNECT_TIMEOUT)
        assert connected is True

        close_event = await first.receive_output(1)
        assert close_event["type"] == "websocket.close"

        assert (
            store.connections[store.pending_key(serial)] is not existing_consumer
        )

        await second.disconnect()
        await first.wait()

    async_to_sync(run_scenario)()


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_assign_connector_rebinds_store_preserves_state():
    async def run_scenario():
        serial = "CP-CONNECTOR-REASSIGN"
        path = f"/{serial}"

        pending_key = store.pending_key(serial)
        aggregate_key = store.identity_key(serial, None)
        connector_key = store.identity_key(serial, 1)

        communicator = WebsocketCommunicator(application, path)
        connected, _ = await communicator.connect(timeout=CONNECT_TIMEOUT)
        assert connected is True

        consumer = store.connections[pending_key]

        boot_notification = [
            2,
            "boot-1",
            "BootNotification",
            {"chargePointModel": "UnitTest", "chargePointVendor": "UnitVendor"},
        ]
        await communicator.send_json_to(boot_notification)
        boot_reply = await communicator.receive_json_from()
        assert boot_reply[0] == 3

        assert store.connections.get(aggregate_key) is consumer
        assert store.connections.get(pending_key) is None

        store.start_session_log(aggregate_key, tx_id=101)
        store.add_session_message(aggregate_key, "boot-message")
        store.add_log(aggregate_key, "pre-connector log", log_type="charger")

        status_notification = [
            2,
            "status-1",
            "StatusNotification",
            {"connectorId": 1, "status": "Available", "errorCode": "NoError"},
        ]
        await communicator.send_json_to(status_notification)
        status_reply = await communicator.receive_json_from()
        assert status_reply[0] == 3

        assert store.connections.get(connector_key) is consumer
        assert store.connections.get(aggregate_key) is None

        charger_logs = list(store.logs["charger"].get(connector_key, []))
        assert any("pre-connector log" in entry for entry in charger_logs)

        session_history = store.history.get(connector_key)
        assert session_history is not None
        assert any("boot-message" in chunk for chunk in session_history["buffer"])

        async def fetch_chargers():
            aggregate = await database_sync_to_async(Charger.objects.get)(
                charger_id=serial, connector_id=None
            )
            connector = await database_sync_to_async(Charger.objects.get)(
                charger_id=serial, connector_id=1
            )
            return aggregate, connector

        aggregate, connector = await fetch_chargers()
        assert aggregate.pk is not None
        assert connector.pk is not None

        await asyncio.sleep(0.05)
        assert communicator.future.done() is False

        await communicator.disconnect()

    async_to_sync(run_scenario)()


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_existing_charger_clears_status_and_refreshes_forwarding(monkeypatch):
    charger = Charger.objects.create(
        charger_id="CP-CLEAR-CACHE",
        connector_id=None,
        last_status="Charging",
        last_error_code="Fault",
        last_status_vendor_info="vendor",
        last_status_timestamp=timezone.now(),
    )

    called: dict[str, object] = {}

    def mock_sync_forwarded_charge_points(*, refresh_forwarders=True):
        called["refresh_forwarders"] = refresh_forwarders
        return 0

    monkeypatch.setattr(
        "apps.ocpp.forwarder.forwarder.sync_forwarded_charge_points",
        mock_sync_forwarded_charge_points,
    )

    async def run_scenario():
        communicator = WebsocketCommunicator(application, f"/{charger.charger_id}")
        connected, _ = await communicator.connect(timeout=CONNECT_TIMEOUT)
        assert connected is True
        await communicator.disconnect()

    async_to_sync(run_scenario)()

    charger.refresh_from_db()

    assert charger.last_status == ""
    assert charger.last_error_code == ""
    assert charger.last_status_vendor_info is None
    assert called["refresh_forwarders"] is False


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
class TestSimulatorLiveServer(ChannelsLiveServerTestCase):
    host = "127.0.0.1"

    def _reset_store(self):
        cache.clear()
        store.connections.clear()
        store.ip_connections.clear()
        store.logs["charger"].clear()
        store.log_names["charger"].clear()
        RateLimit.objects.all().delete()
        cache.clear()

    def setUp(self):
        super().setUp()
        self._reset_store()

    def tearDown(self):
        self._reset_store()
        super().tearDown()

    def test_cp_simulator_connects_with_default_fixture(self):
        call_command(
            "loaddata", "apps/ocpp/fixtures/simulators__localsim_connector_2.json"
        )
        simulator = Simulator.objects.get(default=True)
        config = simulator.as_config()
        config.pre_charge_delay = 0
        config.duration = 0
        config.interval = 0.01
        config.host = self.host
        config.ws_port = self._port

        cp_simulator = ChargePointSimulator(config)

        async_to_sync(cp_simulator._run_session)()

        assert cp_simulator._last_ws_subprotocol == "ocpp1.6"
        assert cp_simulator._last_close_code == 1000
        assert cp_simulator._last_close_reason in ("", None)
        assert cp_simulator._connected.is_set()
        assert cp_simulator._connect_error == "accepted"
        assert cp_simulator.status == "stopped"


def _latest_log_message(key: str) -> str:
    entry = store.logs["charger"][key][-1]
    parts = entry.split(" ", 2)
    return parts[-1] if len(parts) == 3 else entry


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_rejects_invalid_serial_from_path_logs_reason():
    async def run_scenario():
        communicator = WebsocketCommunicator(application, "/<charger_id>")
        connected, close_code = await communicator.connect(timeout=CONNECT_TIMEOUT)
        assert connected is False
        assert close_code == 4003

    async_to_sync(run_scenario)()

    store_key = store.pending_key("<charger_id>")
    message = _latest_log_message(store_key)
    assert "Serial Number placeholder values such as <charger_id> are not allowed." in message


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_rejects_invalid_query_serial_and_logs_details():
    async def run_scenario():
        communicator = WebsocketCommunicator(application, "/?cid=")
        connected, close_code = await communicator.connect(timeout=CONNECT_TIMEOUT)
        assert connected is False
        assert close_code == 4003

    async_to_sync(run_scenario)()

    store_key = store.pending_key("")
    message = _latest_log_message(store_key)
    assert "Serial Number cannot be blank." in message
    assert "query_string='cid='" in message


def _auth_header(username: str, password: str) -> list[tuple[bytes, bytes]]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8"))
    return [(b"authorization", b"Basic " + token)]


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_basic_auth_rejects_when_missing_header():
    user = get_user_model().objects.create_user(username="auth-missing", password="secret")
    charger = Charger.objects.create(charger_id="AUTH-MISSING", connector_id=None, ws_auth_user=user)

    async def run_scenario():
        communicator = WebsocketCommunicator(application, f"/{charger.charger_id}")
        connected, close_code = await communicator.connect(timeout=CONNECT_TIMEOUT)
        assert connected is False
        assert close_code == 4003

    async_to_sync(run_scenario)()

    store_key = store.pending_key(charger.charger_id)
    message = _latest_log_message(store_key)
    assert "HTTP Basic authentication required (credentials missing)" in message


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_basic_auth_rejects_invalid_header_format():
    user = get_user_model().objects.create_user(username="auth-invalid", password="secret")
    charger = Charger.objects.create(charger_id="AUTH-INVALID", connector_id=None, ws_auth_user=user)

    async def run_scenario():
        communicator = WebsocketCommunicator(
            application,
            f"/{charger.charger_id}",
            headers=[(b"authorization", b"Bearer token")],
        )
        connected, close_code = await communicator.connect(timeout=CONNECT_TIMEOUT)
        assert connected is False
        assert close_code == 4003

    async_to_sync(run_scenario)()

    store_key = store.pending_key(charger.charger_id)
    message = _latest_log_message(store_key)
    assert "HTTP Basic authentication header is invalid" in message


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_basic_auth_rejects_invalid_credentials():
    user = get_user_model().objects.create_user(username="auth-fail", password="secret")
    charger = Charger.objects.create(charger_id="AUTH-FAIL", connector_id=None, ws_auth_user=user)

    async def run_scenario():
        communicator = WebsocketCommunicator(
            application,
            f"/{charger.charger_id}",
            headers=_auth_header("auth-fail", "wrong"),
        )
        connected, close_code = await communicator.connect(timeout=CONNECT_TIMEOUT)
        assert connected is False
        assert close_code == 4003

    async_to_sync(run_scenario)()

    store_key = store.pending_key(charger.charger_id)
    message = _latest_log_message(store_key)
    assert "HTTP Basic authentication failed" in message


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_basic_auth_rejects_unauthorized_user():
    authorized = get_user_model().objects.create_user(username="authorized", password="secret")
    unauthorized = get_user_model().objects.create_user(username="unauthorized", password="secret")
    charger = Charger.objects.create(
        charger_id="AUTH-UNAUTH", connector_id=None, ws_auth_user=authorized
    )

    async def run_scenario():
        communicator = WebsocketCommunicator(
            application,
            f"/{charger.charger_id}",
            headers=_auth_header("unauthorized", "secret"),
        )
        connected, close_code = await communicator.connect(timeout=CONNECT_TIMEOUT)
        assert connected is False
        assert close_code == 4003

    async_to_sync(run_scenario)()

    store_key = store.pending_key(charger.charger_id)
    message = _latest_log_message(store_key)
    assert any(
        expected in message
        for expected in [
            "HTTP Basic authentication rejected for unauthorized user",
            "HTTP Basic authentication failed",
        ]
    )


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_basic_auth_accepts_authorized_user():
    user = get_user_model().objects.create_user(username="auth-ok", password="secret")
    charger = Charger.objects.create(charger_id="AUTH-OK", connector_id=None, ws_auth_user=user)

    connection_result: dict[str, object] = {}

    async def run_scenario():
        communicator = WebsocketCommunicator(
            application,
            f"/{charger.charger_id}",
            headers=_auth_header("auth-ok", "secret"),
        )
        connected, close_code = await communicator.connect(timeout=CONNECT_TIMEOUT)
        connection_result["connected"] = connected
        connection_result["close_code"] = close_code
        if connected:
            await communicator.disconnect()

    async_to_sync(run_scenario)()

    store_key = store.pending_key(charger.charger_id)
    entries = list(store.logs.get("charger", {}).get(store_key, []))
    auth_entries = [entry for entry in entries if "HTTP Basic authentication" in entry]
    if connection_result.get("connected"):
        assert not auth_entries
        assert any("Connected" in entry for entry in entries)
    else:
        assert auth_entries or connection_result.get("close_code") != 4003


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_unknown_extension_action_replies_with_empty_call_result():
    async def run_scenario():
        serial = "CP-EXT-ACTION"
        communicator = WebsocketCommunicator(application, f"/{serial}")
        connected, _ = await communicator.connect(timeout=CONNECT_TIMEOUT)
        assert connected is True

        message_id = "ext-call"
        await communicator.send_json_to(
            [2, message_id, "VendorSpecificAction", {"vendorId": "ACME"}]
        )
        response = await communicator.receive_json_from()
        assert response == [3, message_id, {}]

        follow_up_id = "ext-follow"
        await communicator.send_json_to([2, follow_up_id, "AnotherVendorAction", {}])
        follow_up_response = await communicator.receive_json_from()
        assert follow_up_response == [3, follow_up_id, {}]

        await communicator.disconnect()

    async_to_sync(run_scenario)()

    all_entries = [
        entry for buffer in store.logs["charger"].values() for entry in buffer
    ]
    assert any('[3, "ext-call", {}]' in entry for entry in all_entries), all_entries

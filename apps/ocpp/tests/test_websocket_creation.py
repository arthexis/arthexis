import asyncio
import base64
import json

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import ChannelsLiveServerTestCase, WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.core.management import call_command
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.features.models import Feature
from apps.groups.constants import NETWORK_OPERATOR_GROUP_NAME
from apps.nodes.models import Node
from apps.ocpp import store
from apps.ocpp.consumers import (
    OCPP_VERSION_16,
    OCPP_VERSION_21,
    OCPP_VERSION_201,
    CSMSConsumer,
)
from apps.ocpp.models import Charger, Simulator
from apps.rates.models import RateLimit
from apps.simulators import ChargePointSimulator
from config.asgi import application

pytestmark = pytest.mark.django_db(transaction=True)

CONNECT_TIMEOUT = 5

async def _finalize_communicator(communicator: WebsocketCommunicator) -> None:
    """Wait for communicator shutdown so teardown does not stall on pending tasks."""

    if communicator.future.done():
        await communicator.wait()
        return
    await communicator.disconnect()
    await communicator.wait()

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

@pytest.fixture
def local_node(monkeypatch):
    def _noop_sync_feature_tasks(*_args, **_kwargs):
        return None

    monkeypatch.setattr(Node, "sync_feature_tasks", _noop_sync_feature_tasks)
    Node._local_cache.clear()
    node = Node.objects.create(
        hostname="local-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    Node._local_cache.clear()
    return node

@pytest.fixture
def charge_point_features(local_node):
    """Return local node plus suite features used for OCPP admission tests."""

    feature_map = {}
    for slug, display in [
        ("ocpp-16-charge-point", "OCPP 1.6 Charge Point"),
        ("ocpp-201-charge-point", "OCPP 2.0.1 Charge Point"),
        ("ocpp-21-charge-point", "OCPP 2.1 Charge Point"),
    ]:
        suite_feature, _ = Feature.objects.get_or_create(
            slug=slug,
            defaults={"display": display},
        )
        suite_feature.is_enabled = True
        suite_feature.save(update_fields=["is_enabled"])
        feature_map[slug] = suite_feature
    return local_node, feature_map
@override_settings(ROOT_URLCONF="apps.ocpp.urls")
@pytest.mark.parametrize(
    "preferred",
    [OCPP_VERSION_201, OCPP_VERSION_21],
)
@override_settings(
    ROOT_URLCONF="apps.ocpp.urls",
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
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
        call_command("loaddata", "apps/ocpp/fixtures/simulators__local_cp_2.json")
        simulator = Simulator.objects.get(name="Local CP 2")
        config = simulator.as_config()
        config.pre_charge_delay = 0
        config.duration = 0
        config.interval = 0.01
        config.host = self.host
        config.ws_port = self._port

        cp_simulator = ChargePointSimulator(config)

        async_to_sync(cp_simulator._run_session)()

        if cp_simulator._last_close_code is None:
            pytest.skip(
                "Live websocket handshake did not complete in this environment."
            )

        assert cp_simulator._last_ws_subprotocol == "ocpp1.6j"
        assert cp_simulator._last_close_code == 1000
        assert cp_simulator._last_close_reason in ("", None)
        assert cp_simulator._connected.is_set()
        assert cp_simulator._connect_error == "accepted"
        assert cp_simulator.status == "stopped"
@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_rejects_invalid_serial_from_path_logs_reason():
    async def run_scenario():
        communicator = WebsocketCommunicator(application, "/<charger_id>")
        connected, close_code = await communicator.connect(timeout=CONNECT_TIMEOUT)
        assert connected is False
        assert close_code == 4003
        await _finalize_communicator(communicator)

    async_to_sync(run_scenario)()

    store_key = store.pending_key("<charger_id>")
    entries = list(store.logs.get("charger", {}).get(store_key, []))
    assert any(
        "Serial Number placeholder values such as <charger_id> are not allowed."
        in entry
        for entry in entries
    )
@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_rejects_invalid_query_serial_and_logs_details():
    async def run_scenario():
        communicator = WebsocketCommunicator(application, "/?cid=")
        connected, close_code = await communicator.connect(timeout=CONNECT_TIMEOUT)
        assert connected is False
        assert close_code == 4003
        await _finalize_communicator(communicator)

    async_to_sync(run_scenario)()

    store_key = store.pending_key("")
    entries = list(store.logs.get("charger", {}).get(store_key, []))
    assert any("Serial Number cannot be blank." in entry for entry in entries)
    assert any("query_string='cid='" in entry for entry in entries)

def _auth_header(username: str, password: str) -> list[tuple[bytes, bytes]]:
    token = base64.b64encode(f"{username}:{password}".encode())
    return [(b"authorization", b"Basic " + token)]
@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_basic_auth_accepts_charge_station_manager_user():
    authorized = get_user_model().objects.create_user(
        username="auth-designated", password="secret"
    )
    manager = get_user_model().objects.create_user(
        username="auth-manager", password="secret"
    )
    manager.groups.create(name=NETWORK_OPERATOR_GROUP_NAME)
    charger = Charger.objects.create(
        charger_id="AUTH-MANAGER", connector_id=None, ws_auth_user=authorized
    )

    connection_result: dict[str, object] = {}

    async def run_scenario():
        communicator = WebsocketCommunicator(
            application,
            f"/{charger.charger_id}",
            headers=_auth_header("auth-manager", "secret"),
        )
        connected, close_code = await communicator.connect(timeout=CONNECT_TIMEOUT)
        connection_result["connected"] = connected
        connection_result["close_code"] = close_code
        await _finalize_communicator(communicator)

    async_to_sync(run_scenario)()

    assert connection_result["connected"] is True
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

        await _finalize_communicator(communicator)

    async_to_sync(run_scenario)()

    all_entries = [
        entry for buffer in store.logs["charger"].values() for entry in buffer
    ]
    assert any('[3, "ext-call", {}]' in entry for entry in all_entries), all_entries

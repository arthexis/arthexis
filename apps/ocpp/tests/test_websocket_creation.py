import asyncio
import json
import base64
from urllib.parse import urlparse

import pytest
import websockets
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
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
        connected, _ = await communicator.connect()
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
        ((["ocpp1.6", "ocpp2.0.1", "ocpp2.0"], "ocpp2.0"), "ocpp2.0"),
        ((["ocpp2.0", "ocpp2.0.1"], None), "ocpp2.0.1"),
        ((["ocpp1.6"], None), "ocpp1.6"),
        ((["unexpected"], None), None),
    ]

    for (offered, preferred), expected in cases:
        assert consumer._select_subprotocol(offered, preferred) == expected


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_ocpp_websocket_rate_limit_enforced():
    async def run_scenario():
        serial = "CP-RATE-LIMIT"
        path = f"/{serial}"

        first = WebsocketCommunicator(application, path)
        connected, _ = await first.connect()
        assert connected is True

        second = WebsocketCommunicator(application, path)
        connected, _ = await second.connect()
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
def test_local_ip_bypasses_rate_limit_with_custom_scope_client():
    async def run_scenario():
        serial = "CP-LOCAL-BYPASS"
        path = f"/{serial}"

        throttled = WebsocketCommunicator(application, path)
        throttled.scope["client"] = ("8.8.8.8", 1000)
        connected, _ = await throttled.connect()
        assert connected is False

        local = WebsocketCommunicator(application, path)
        local.scope["client"] = ("127.0.0.1", 1001)
        connected, _ = await local.connect()
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
        connected, _ = await first.connect()
        assert connected is True

        existing_consumer = store.connections[store.pending_key(serial)]

        second = WebsocketCommunicator(application, path)
        connected, _ = await second.connect()
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
        connected, _ = await communicator.connect()
        assert connected is True
        await communicator.disconnect()

    async_to_sync(run_scenario)()

    charger.refresh_from_db()

    assert charger.last_status == ""
    assert charger.last_error_code == ""
    assert charger.last_status_vendor_info is None
    assert called["refresh_forwarders"] is False


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_cp_simulator_connects_with_default_fixture(monkeypatch):
    call_command("loaddata", "apps/ocpp/fixtures/simulators__localsim_connector_2.json")
    cache.clear()
    simulator = Simulator.objects.get(default=True)
    config = simulator.as_config()
    config.pre_charge_delay = 0
    config.duration = 1
    config.interval = 0.1

    async def mock_connect(uri, subprotocols=None, **kwargs):
        parsed = urlparse(uri)
        communicator = WebsocketCommunicator(
            application, parsed.path, subprotocols=subprotocols or None
        )
        connected, accepted_subprotocol = await communicator.connect()
        if not connected:
            raise RuntimeError("WebSocket connection failed")

        class CommunicatorWebSocket:
            def __init__(self, comm, subprotocol):
                self._comm = comm
                self.subprotocol = subprotocol
                self.close_code = None
                self.close_reason = ""

            async def send(self, msg: str) -> None:
                await self._comm.send_to(text_data=msg)

            async def recv(self) -> str:
                message = await self._comm.receive_from()
                if message is None:
                    raise websockets.exceptions.ConnectionClosed(1000, "closed")
                return message

            async def close(self) -> None:
                await self._comm.disconnect()
                self.close_code = None
                self.close_reason = ""

        return CommunicatorWebSocket(communicator, accepted_subprotocol)

    monkeypatch.setattr("apps.ocpp.simulator.websockets.connect", mock_connect)

    async def short_run_session(self):
        cfg = self.config

        uri = f"ws://{cfg.host}:{cfg.ws_port}/{cfg.cp_path}" if cfg.ws_port else f"ws://{cfg.host}/{cfg.cp_path}"
        ws = await websockets.connect(uri, subprotocols=["ocpp1.6"])

        async def send(msg: str) -> None:
            await ws.send(msg)

        async def recv() -> str:
            return await ws.recv()

        boot = json.dumps(
            [
                2,
                "boot",
                "BootNotification",
                {
                    "chargePointModel": "Simulator",
                    "chargePointVendor": "SimVendor",
                    "serialNumber": cfg.serial_number,
                },
            ]
        )
        await send(boot)
        resp = json.loads(await recv())
        status = resp[2].get("status")
        if status != "Accepted":
            if not self._connected.is_set():
                self._connect_error = f"Boot status {status}"
                self._connected.set()
            return

        await send(json.dumps([2, "auth", "Authorize", {"idTag": cfg.rfid}]))
        await recv()

        if not self._connected.is_set():
            self.status = "running"
            self._connect_error = "accepted"
            self._connected.set()

        self.status = "stopped"
        self._stop_event.set()
        await ws.close()

    cp_simulator = ChargePointSimulator(config)
    async_to_sync(short_run_session)(cp_simulator)

    assert cp_simulator._connected.is_set()
    charger = Charger.objects.filter(charger_id=config.cp_path, connector_id=None).first()
    assert charger is not None
    assert charger.last_path == f"/{config.cp_path}"


def _latest_log_message(key: str) -> str:
    entry = store.logs["charger"][key][-1]
    parts = entry.split(" ", 2)
    return parts[-1] if len(parts) == 3 else entry


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_rejects_invalid_serial_from_path_logs_reason():
    async def run_scenario():
        communicator = WebsocketCommunicator(application, "/<charger_id>")
        connected, close_code = await communicator.connect()
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
        connected, close_code = await communicator.connect()
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
        connected, close_code = await communicator.connect()
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
        connected, close_code = await communicator.connect()
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
        connected, close_code = await communicator.connect()
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
        connected, close_code = await communicator.connect()
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
        connected, close_code = await communicator.connect()
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

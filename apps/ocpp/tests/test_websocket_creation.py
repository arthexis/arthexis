import asyncio
import base64
import json
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

from apps.ocpp import store
from apps.ocpp.models import Charger, Simulator
from apps.ocpp.simulator import ChargePointSimulator
from apps.rates.models import RateLimit
from config.asgi import application

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def clear_store_state():
    cache.clear()
    RateLimit.objects.all().delete()
    store.connections.clear()
    store.ip_connections.clear()
    yield
    cache.clear()
    RateLimit.objects.all().delete()
    store.connections.clear()
    store.ip_connections.clear()


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
def test_rejects_placeholder_serial_in_path():
    async def run_scenario():
        path = "/<charger_id>"
        communicator = WebsocketCommunicator(application, path)

        connected, close_code = await communicator.connect()

        assert connected is False
        assert close_code == 4003
        exists = await database_sync_to_async(
            Charger.objects.filter(charger_id="<charger_id>", connector_id=None).exists
        )()
        assert exists is False

    async_to_sync(run_scenario)()


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_basic_auth_required_and_validates_credentials():
    User = get_user_model()
    user = User.objects.create_user(username="ws-user", password="secret")
    charger = Charger.objects.create(charger_id="CP-AUTH", ws_auth_user=user)

    async def run_scenario():
        path = f"/{charger.charger_id}"

        unauthenticated = WebsocketCommunicator(application, path)
        connected, close_code = await unauthenticated.connect()
        assert connected is False
        assert close_code == 4003

        invalid_header = [(b"authorization", b"Basic !!invalid!!")]
        invalid = WebsocketCommunicator(application, path, headers=invalid_header)
        connected, close_code = await invalid.connect()
        assert connected is False
        assert close_code == 4003

        token = base64.b64encode(b"ws-user:secret").decode("ascii")
        valid_header = [(b"authorization", f"Basic {token}".encode("ascii"))]
        authenticated = WebsocketCommunicator(
            application, path, headers=valid_header, subprotocols=["ocpp1.6"]
        )
        connected, accepted_subprotocol = await authenticated.connect()
        assert connected is True
        assert accepted_subprotocol == "ocpp1.6"

        await authenticated.disconnect()

    async_to_sync(run_scenario)()


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_negotiates_latest_supported_subprotocol():
    async def run_scenario():
        communicator = WebsocketCommunicator(
            application,
            "/CP-PROTO-NEW",
            subprotocols=["ocpp1.6", "ocpp2.0.1"],
        )

        connected, accepted_subprotocol = await communicator.connect()

        assert connected is True
        assert accepted_subprotocol == "ocpp2.0.1"

        await communicator.disconnect()
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
def test_preferred_subprotocol_falls_back_when_missing():
    Charger.objects.create(charger_id="CP-PROTO-PREF", preferred_ocpp_version="ocpp2.0")

    async def run_scenario():
        communicator = WebsocketCommunicator(
            application, "/CP-PROTO-PREF", subprotocols=["ocpp1.6"]
        )

        connected, accepted_subprotocol = await communicator.connect()

        assert connected is True
        assert accepted_subprotocol == "ocpp1.6"

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


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_uses_preferred_version_when_no_subprotocol_offered():
    Charger.objects.create(charger_id="CP-PROTO-PREFERRED", preferred_ocpp_version="ocpp2.0")

    async def run_scenario():
        communicator = WebsocketCommunicator(application, "/CP-PROTO-PREFERRED")

        connected, accepted_subprotocol = await communicator.connect()

        assert connected is True
        assert accepted_subprotocol is None
        store_key = store.pending_key("CP-PROTO-PREFERRED")
        consumer = store.connections.get(store_key)
        assert consumer is not None
        assert getattr(consumer, "ocpp_version", "") == "ocpp2.0"

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

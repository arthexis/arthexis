import asyncio
import json
from urllib.parse import urlparse

import pytest
import websockets
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.urls import reverse
from django.test.utils import override_settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.core.management import call_command

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
    yield
    cache.clear()
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


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_connect_without_subprotocol_uses_preference_and_accepts():
    existing = Charger.objects.create(
        charger_id="CP-NO-SUBPROTO",
        connector_id=None,
        preferred_ocpp_version="ocpp2.0",
    )

    async def run_scenario():
        communicator = WebsocketCommunicator(application, f"/{existing.charger_id}")
        connected, accepted_subprotocol = await communicator.connect()

        assert connected is True
        assert accepted_subprotocol is None

        store_key = store.pending_key(existing.charger_id)
        consumer = store.connections.get(store_key)
        assert consumer is not None
        assert consumer.ocpp_version == "ocpp2.0"

        await communicator.disconnect()

    async_to_sync(run_scenario)()


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_connect_prefers_latest_offered_subprotocol():
    serial = "CP-SUBPROTO-LATEST"

    async def run_scenario():
        communicator = WebsocketCommunicator(
            application, f"/{serial}", subprotocols=["ocpp2.0", "ocpp2.0.1"]
        )

        connected, accepted_subprotocol = await communicator.connect()
        assert connected is True
        assert accepted_subprotocol == "ocpp2.0.1"

        store_key = store.pending_key(serial)
        consumer = store.connections.get(store_key)
        assert consumer is not None
        assert consumer.ocpp_version == "ocpp2.0.1"

        await communicator.disconnect()

    async_to_sync(run_scenario)()

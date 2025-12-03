import asyncio
import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.urls import reverse
from django.test.utils import override_settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache

from apps.ocpp import store
from apps.ocpp.models import Charger
from apps.rates.models import RateLimit
from config.asgi import application

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def clear_store_state():
    store.connections.clear()
    store.ip_connections.clear()
    yield
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

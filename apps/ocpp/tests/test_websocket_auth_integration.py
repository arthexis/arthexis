import base64

import pytest
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.conf import settings
from django.test.utils import override_settings

from apps.ocpp import store
from apps.ocpp.models import Charger
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


@pytest.fixture
def authorized_charger():
    settings.ROOT_URLCONF = "apps.ocpp.urls"
    settings.AUTHENTICATION_BACKENDS = [
        "django.contrib.auth.backends.ModelBackend",
    ]
    User = get_user_model()
    authorized = User.objects.create_user(username="authorized-ws", password="secret")
    unauthorized = User.objects.create_user(
        username="unauthorized-ws", password="secret"
    )
    charger = Charger.objects.create(
        charger_id="AUTH-REQUIRED", connector_id=None, ws_auth_user=authorized
    )
    return {
        "charger": charger,
        "authorized": authorized,
        "unauthorized": unauthorized,
    }


def _auth_header(username: str, password: str) -> list[tuple[bytes, bytes]]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8"))
    return [(b"authorization", b"Basic " + token)]


def _latest_log_message(key: str) -> str:
    entry = store.logs["charger"][key][-1]
    parts = entry.split(" ", 2)
    return parts[-1] if len(parts) == 3 else entry


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_requires_ws_auth_rejects_missing_header(authorized_charger):
    charger = authorized_charger["charger"]

    async def run_scenario():
        communicator = WebsocketCommunicator(application, f"/{charger.charger_id}")
        connected, close_code = await communicator.connect()
        assert connected is False
        assert close_code == 4003

    async_to_sync(run_scenario)()

    store_key = store.pending_key(charger.charger_id)
    message = _latest_log_message(store_key)
    assert "Rejected connection: HTTP Basic authentication required" in message


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_requires_ws_auth_rejects_malformed_basic_header(authorized_charger):
    charger = authorized_charger["charger"]

    async def run_scenario():
        communicator = WebsocketCommunicator(
            application,
            f"/{charger.charger_id}",
            headers=[(b"authorization", b"Basic invalid")],
        )
        connected, close_code = await communicator.connect()
        assert connected is False
        assert close_code == 4003

    async_to_sync(run_scenario)()

    store_key = store.pending_key(charger.charger_id)
    message = _latest_log_message(store_key)
    assert "Rejected connection: HTTP Basic authentication header is invalid" in message


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_requires_ws_auth_rejects_invalid_credentials(authorized_charger):
    charger = authorized_charger["charger"]

    async def run_scenario():
        communicator = WebsocketCommunicator(
            application,
            f"/{charger.charger_id}",
            headers=_auth_header("authorized-ws", "wrong"),
        )
        connected, close_code = await communicator.connect()
        assert connected is False
        assert close_code == 4003

    async_to_sync(run_scenario)()

    store_key = store.pending_key(charger.charger_id)
    message = _latest_log_message(store_key)
    assert "Rejected connection: HTTP Basic authentication failed" in message


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_requires_ws_auth_rejects_unauthorized_user(authorized_charger):
    charger = authorized_charger["charger"]
    unauthorized = authorized_charger["unauthorized"]

    async def run_scenario():
        communicator = WebsocketCommunicator(
            application,
            f"/{charger.charger_id}",
            headers=_auth_header(unauthorized.username, "secret"),
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
            "Rejected connection: HTTP Basic authentication rejected for unauthorized user",
            "Rejected connection: HTTP Basic authentication failed",
        ]
    )


@override_settings(ROOT_URLCONF="apps.ocpp.urls")
def test_requires_ws_auth_accepts_authorized_user(authorized_charger):
    charger = authorized_charger["charger"]
    authorized = authorized_charger["authorized"]

    async def run_scenario():
        communicator = WebsocketCommunicator(
            application,
            f"/{charger.charger_id}",
            headers=_auth_header(authorized.username, "secret"),
        )
        connected, close_code = await communicator.connect()
        assert connected is True
        assert close_code is None
        await communicator.disconnect()

    async_to_sync(run_scenario)()

    store_key = store.pending_key(charger.charger_id)
    entries = list(store.logs["charger"].get(store_key, []))
    assert any("Connected" in entry for entry in entries)
    assert not any("Rejected connection" in entry for entry in entries)

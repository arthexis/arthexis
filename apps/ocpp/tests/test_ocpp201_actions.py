import json

import pytest

from apps.ocpp import store
from apps.ocpp.tasks import request_charge_point_log
from apps.ocpp.views import actions
from apps.ocpp.views.common import ActionContext, ActionCall


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

    monkeypatch.setattr(actions, "ChargingProfile", type("CPModel", (), {"objects": QueryStub()}))

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


@pytest.mark.django_db
def test_get_log_supports_ocpp201(monkeypatch, ws):
    from apps.ocpp.models import Charger

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

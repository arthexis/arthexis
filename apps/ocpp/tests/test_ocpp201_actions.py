import json

import pytest

from apps.ocpp import store
from apps.ocpp.tasks import request_charge_point_log
from apps.ocpp.views import charger as charger_module
from apps.ocpp.views.misc import ActionContext, ActionCall


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
def stub_store(monkeypatch):
    pending: dict[str, list[tuple[tuple, dict]]] = {
        "register": [],
        "timeouts": [],
    }

    monkeypatch.setattr(store, "register_pending_call", lambda *a, **k: pending["register"].append((a, k)))
    monkeypatch.setattr(store, "schedule_call_timeout", lambda *a, **k: pending["timeouts"].append((a, k)))
    monkeypatch.setattr(store, "identity_key", lambda *a, **k: "log-key")
    monkeypatch.setattr(store, "add_log", lambda *a, **k: None)

    yield pending


def test_unlock_connector_supports_ocpp201(ws, stub_store):
    context = ActionContext("CID", 2, charger=None, ws=ws, log_key="log-key")
    result = charger_module._handle_unlock_connector(context, {})

    assert isinstance(result, ActionCall)
    assert ws.sent and json.loads(ws.sent[0])[2] == "UnlockConnector"
    assert stub_store["register"]


def test_send_local_list_supports_ocpp201(ws, stub_store):
    charger_stub = type("ChargerStub", (), {"local_auth_list_version": 4})
    context = ActionContext("CID", None, charger=charger_stub, ws=ws, log_key="log-key")
    result = charger_module._handle_send_local_list(
        context,
        {"localAuthorizationList": [{"idTag": "ABC"}]},
    )

    assert isinstance(result, ActionCall)
    assert ws.sent and json.loads(ws.sent[0])[2] == "SendLocalList"
    assert stub_store["register"]


def test_set_charging_profile_supports_ocpp201(monkeypatch, ws, stub_store):
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
        charger_module, "ChargingProfile", type("CPModel", (), {"objects": QueryStub()})
    )

    context = ActionContext("CID", 1, charger=None, ws=ws, log_key="log-key")
    result = charger_module._handle_set_charging_profile(
        context, {"profileId": profile.charging_profile_id}
    )

    assert isinstance(result, ActionCall)
    assert ws.sent and json.loads(ws.sent[0])[2] == "SetChargingProfile"
    assert stub_store["timeouts"]


@pytest.mark.django_db
def test_get_log_supports_ocpp201(monkeypatch, ws, stub_store):
    from apps.ocpp.models import Charger

    monkeypatch.setattr(Charger, "get_absolute_url", lambda self: "/charger/")
    monkeypatch.setattr(Charger, "_full_url", lambda self: "https://example.com/charger/")

    charger = Charger.objects.create(charger_id="CID-LOG")

    monkeypatch.setattr(store, "get_connection", lambda *_args, **_kwargs: ws)
    monkeypatch.setattr(store, "start_log_capture", lambda *_a, **_k: "capture-key")
    monkeypatch.setattr(store, "finalize_log_capture", lambda *_a, **_k: None)

    request_pk = request_charge_point_log(charger.pk, log_type="Diagnostics")

    assert request_pk
    assert ws.sent and json.loads(ws.sent[0])[2] == "GetLog"
    assert stub_store["register"]

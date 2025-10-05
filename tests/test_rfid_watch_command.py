import pytest
from io import StringIO
from django.core.management import call_command
from django.core import management


pytestmark = [
    pytest.mark.role("Control"),
    pytest.mark.feature("rfid-scanner"),
]


def _register_rfid_watch(monkeypatch):
    original_get_commands = management.get_commands

    def patched_get_commands():
        commands = original_get_commands()
        commands["rfid_watch"] = "ocpp.rfid"
        return commands

    monkeypatch.setattr(management, "get_commands", patched_get_commands)


def test_rfid_watch_start_enables_when_running(monkeypatch):
    _register_rfid_watch(monkeypatch)

    start_calls = []
    stop_calls = []

    def fake_start():
        start_calls.append(True)

    def fake_stop():
        stop_calls.append(True)

    monkeypatch.setattr("ocpp.rfid.always_on.start", fake_start)
    monkeypatch.setattr("ocpp.rfid.always_on.stop", fake_stop)
    monkeypatch.setattr("ocpp.rfid.always_on.is_running", lambda: True)

    out = StringIO()
    call_command("rfid_watch", stdout=out)

    assert start_calls == [True]
    assert stop_calls == []
    assert out.getvalue().strip() == "RFID watch enabled"


def test_rfid_watch_stop_disables(monkeypatch):
    _register_rfid_watch(monkeypatch)

    start_calls = []
    stop_calls = []

    monkeypatch.setattr("ocpp.rfid.always_on.start", lambda: start_calls.append(True))
    monkeypatch.setattr("ocpp.rfid.always_on.stop", lambda: stop_calls.append(True))
    monkeypatch.setattr("ocpp.rfid.always_on.is_running", lambda: True)

    out = StringIO()
    call_command("rfid_watch", "--stop", stdout=out)

    assert start_calls == []
    assert stop_calls == [True]
    assert out.getvalue().strip() == "RFID watch disabled"


def test_rfid_watch_reports_disabled_when_not_running(monkeypatch):
    _register_rfid_watch(monkeypatch)

    state = {"start_called": False}

    def fake_start():
        state["start_called"] = True

    def fake_is_running():
        assert state["start_called"] is True
        return False

    monkeypatch.setattr("ocpp.rfid.always_on.start", fake_start)
    monkeypatch.setattr("ocpp.rfid.always_on.stop", lambda: None)
    monkeypatch.setattr("ocpp.rfid.always_on.is_running", fake_is_running)

    out = StringIO()
    call_command("rfid_watch", stdout=out)

    assert state["start_called"] is True
    assert out.getvalue().strip() == "RFID watch disabled"

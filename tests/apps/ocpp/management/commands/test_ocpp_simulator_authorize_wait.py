import json

import pytest
from django.core.management import CommandError, call_command


def _active(timeout=30.0):
    return {"charger": "GW001", "authorization_timeout": timeout}


def test_authorize_returns_submission_without_wait(monkeypatch, capsys):
    seen = []

    async def fake_send_control(charger, request):
        seen.append((charger, request))
        return {
            "ok": True,
            "charger": charger,
            "request_id": "req-1",
            "submitted": True,
            "completed": False,
        }

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.active_session",
        lambda: _active(),
    )
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.send_control",
        fake_send_control,
    )

    call_command("ocpp_simulator", "authorize", "TAG-1")

    assert seen == [("GW001", {"action": "authorize", "id_tag": "TAG-1"})]
    output = json.loads(capsys.readouterr().out)
    assert output["request_id"] == "req-1"
    assert output["submitted"] is True
    assert output["completed"] is False


def test_authorize_wait_uses_profile_timeout(monkeypatch, capsys):
    seen = []

    async def fake_send_control(charger, request):
        seen.append((charger, request))
        if request["action"] == "authorize":
            return {
                "ok": True,
                "charger": charger,
                "request_id": "req-2",
                "submitted": True,
                "completed": False,
            }
        return {
            "ok": True,
            "charger": charger,
            "request_id": request["request_id"],
            "completed": True,
            "authorization": "Accepted",
            "error": None,
        }

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.active_session",
        lambda: _active(17.5),
    )
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.send_control",
        fake_send_control,
    )

    call_command("ocpp_simulator", "authorize", "TAG-2", "--wait")

    assert seen == [
        ("GW001", {"action": "authorize", "id_tag": "TAG-2"}),
        (
            "GW001",
            {"action": "wait-result", "request_id": "req-2", "timeout": 17.5},
        ),
    ]
    output = json.loads(capsys.readouterr().out)
    assert output["request_id"] == "req-2"
    assert output["submitted"] is True
    assert output["completed"] is True
    assert output["authorization"] == "Accepted"


def test_authorize_wait_explicit_timeout_overrides_profile(monkeypatch, capsys):
    seen = []

    async def fake_send_control(charger, request):
        seen.append(request)
        if request["action"] == "authorize":
            return {
                "ok": True,
                "charger": charger,
                "request_id": "req-3",
                "submitted": True,
                "completed": False,
            }
        return {
            "ok": True,
            "charger": charger,
            "request_id": "req-3",
            "completed": False,
            "timed_out": True,
        }

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.active_session",
        lambda: _active(60.0),
    )
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.send_control",
        fake_send_control,
    )

    call_command(
        "ocpp_simulator",
        "authorize",
        "TAG-3",
        "--wait",
        "--timeout",
        "2.5",
    )

    assert seen[1] == {
        "action": "wait-result",
        "request_id": "req-3",
        "timeout": 2.5,
    }
    output = json.loads(capsys.readouterr().out)
    assert output["timed_out"] is True
    assert output["submitted"] is True


def test_result_reads_late_completion_by_request_id(monkeypatch, capsys):
    seen = []

    async def fake_send_control(charger, request):
        seen.append((charger, request))
        return {
            "ok": True,
            "charger": charger,
            "request_id": "req-late",
            "completed": True,
            "authorization": "Accepted",
            "error": None,
        }

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.active_session",
        lambda: _active(),
    )
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.send_control",
        fake_send_control,
    )

    call_command("ocpp_simulator", "result", "req-late")

    assert seen == [
        ("GW001", {"action": "result", "request_id": "req-late"})
    ]
    assert json.loads(capsys.readouterr().out)["authorization"] == "Accepted"


def test_result_wait_uses_profile_timeout(monkeypatch, capsys):
    seen = []

    async def fake_send_control(charger, request):
        seen.append(request)
        return {
            "ok": True,
            "charger": charger,
            "request_id": "req-pending",
            "completed": False,
            "timed_out": True,
        }

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.active_session",
        lambda: _active(9.0),
    )
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.send_control",
        fake_send_control,
    )

    call_command("ocpp_simulator", "result", "req-pending", "--wait")

    assert seen == [
        {"action": "wait-result", "request_id": "req-pending", "timeout": 9.0}
    ]
    assert json.loads(capsys.readouterr().out)["timed_out"] is True


def test_authorize_timeout_requires_wait(monkeypatch):
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.active_session",
        lambda: _active(),
    )

    with pytest.raises(CommandError, match="--timeout requires --wait"):
        call_command(
            "ocpp_simulator",
            "authorize",
            "TAG-4",
            "--timeout",
            "1",
        )

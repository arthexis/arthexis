import json

import pytest
from django.core.management import CommandError, call_command

from apps.ocpp.management.commands.ocpp_simulator import Command
from apps.ocpp.simulator.network import LiveSimulatorError


class FakeController:
    def __init__(self):
        self.started = 0
        self.stopped = 0

    def ensure_started(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


def test_boot_provisions_service_and_hands_effective_config_to_supervisor(
    tmp_path, monkeypatch, capsys
):
    controller = FakeController()
    captured = {}

    async def fake_service_control(request):
        captured.update(request)
        return {
            "ok": True,
            "service": "arthexis-simulator",
            "charger": request["config"]["charger"],
            "endpoint": request["config"]["url"],
            "open": True,
            "boot": "Accepted",
            "lifecycle": "on-demand",
        }

    monkeypatch.setattr(Command, "_service_controller", staticmethod(lambda: controller))
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.send_service_control",
        fake_service_control,
    )
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.active_session", lambda: None
    )
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.create_session_evidence",
        lambda *args, **kwargs: tmp_path,
    )

    call_command(
        "ocpp_simulator",
        "boot",
        "ws://127.0.0.1:9000",
        "--charger",
        "GW001",
    )

    assert controller.started == 1
    assert captured["action"] == "boot"
    assert captured["config"]["charger"] == "GW001"
    assert captured["config"]["allow_insecure_ws"] is True
    assert captured["idle_timeout"] == 0.0
    payload = json.loads(capsys.readouterr().out)
    assert payload["service"] == "arthexis-simulator"
    assert payload["open"] is True


def test_failed_boot_stops_service_and_cleans_session(tmp_path, monkeypatch):
    controller = FakeController()

    async def fail_service_control(request):
        raise LiveSimulatorError("boot failed")

    monkeypatch.setattr(Command, "_service_controller", staticmethod(lambda: controller))
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.send_service_control",
        fail_service_control,
    )
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.active_session", lambda: None
    )
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.create_session_evidence",
        lambda *args, **kwargs: tmp_path,
    )

    with pytest.raises(CommandError, match="boot failed"):
        call_command(
            "ocpp_simulator",
            "boot",
            "ws://127.0.0.1:9000",
            "--charger",
            "GW001",
        )

    assert controller.started == 1
    assert controller.stopped == 1


def test_stop_asks_supervisor_to_close_worker_then_stops_unit(monkeypatch, capsys):
    controller = FakeController()
    captured = {}

    async def fake_service_control(request):
        captured.update(request)
        return {
            "ok": True,
            "service": "arthexis-simulator",
            "charger": "GW001",
            "closed": True,
        }

    monkeypatch.setattr(Command, "_service_controller", staticmethod(lambda: controller))
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.send_service_control",
        fake_service_control,
    )

    call_command("ocpp_simulator", "stop", "--charger", "GW001")

    assert captured == {"action": "stop", "charger": "GW001"}
    assert controller.stopped == 1
    assert json.loads(capsys.readouterr().out)["closed"] is True

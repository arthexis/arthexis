import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.management import CommandError, call_command

from apps.ocpp.management.commands.ocpp_simulator import Command
from apps.ocpp.simulator.network import LiveSimulatorConfig
from apps.ocpp.simulator.worker import (
    LiveSimulatorWorker,
    error_path,
    runtime_dir,
    session_path,
    socket_path,
)


def insecure_config(**kwargs):
    return LiveSimulatorConfig(allow_insecure_ws=True, **kwargs)


class FakeSimulator:
    def __init__(self, *, authorization="Accepted") -> None:
        self.connected = False
        self.authorization = authorization
        self.reconnects = 0
        self.calls = []
        self.heartbeat_started = None
        self.release_heartbeat = None

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def reconnect(self) -> None:
        self.reconnects += 1
        self.connected = True

    async def boot(self):
        return SimpleNamespace(status="Accepted", interval=60)

    async def authorize(self, id_tag):
        self.calls.append(("Authorize", id_tag))
        return self.authorization

    async def call(self, action, payload):
        self.calls.append((action, payload))
        if action == "Heartbeat" and self.heartbeat_started is not None:
            self.heartbeat_started.set()
            await self.release_heartbeat.wait()
        return {"currentTime": "now"}


def worker_with(fake):
    return LiveSimulatorWorker(
        insecure_config(url="ws://example.test", charger="GWAY001"),
        simulator_factory=lambda config: fake,
    )


def test_worker_authorize_reuses_live_connection():
    async def exercise():
        fake = FakeSimulator(authorization="Invalid")
        worker = worker_with(fake)
        await worker.connect_and_boot()

        response = await worker.dispatch(
            {"action": "authorize", "id_tag": "UNKNOWN001"}
        )

        assert response["authorization"] == "Invalid"
        assert fake.calls == [("Authorize", "UNKNOWN001")]

    asyncio.run(exercise())


def test_worker_reconnect_reboots_same_charger():
    async def exercise():
        fake = FakeSimulator()
        worker = worker_with(fake)
        await worker.connect_and_boot()

        response = await worker.dispatch({"action": "reconnect"})

        assert response["reconnects"] == 1
        assert fake.reconnects == 1
        assert fake.connected is True

    asyncio.run(exercise())


def test_worker_close_returns_closed_result():
    async def exercise():
        worker = worker_with(FakeSimulator())
        response = await worker.dispatch({"action": "close"})
        assert response == {"ok": True, "charger": "GWAY001", "closed": True}

    asyncio.run(exercise())


def test_runtime_directory_is_owner_only(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o777)
    monkeypatch.setenv("OCPP_SIMULATOR_RUNTIME_DIR", str(root))
    secured = runtime_dir()
    assert secured.stat().st_mode & 0o777 == 0o700


def test_authorize_requires_open_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("OCPP_SIMULATOR_RUNTIME_DIR", str(tmp_path))
    with pytest.raises(CommandError, match="is not open"):
        call_command(
            "ocpp_simulator",
            "authorize",
            "--charger",
            "MISSING",
            "--id-tag",
            "X",
        )


def test_open_rejects_plaintext_without_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("OCPP_SIMULATOR_RUNTIME_DIR", str(tmp_path))
    with pytest.raises(CommandError, match="allow-insecure-ws"):
        call_command(
            "ocpp_simulator",
            "open",
            "--url",
            "ws://example.test:9000",
            "--charger",
            "GWAY001",
        )


def test_startup_failure_terminates_worker_and_cleans_artifacts(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OCPP_SIMULATOR_RUNTIME_DIR", str(tmp_path))
    charger = "GWAY001"
    for path in (session_path(charger), socket_path(charger), error_path(charger)):
        path.write_text("stale")

    process = Mock()
    process.poll.return_value = None
    Command._stop_starting_worker(process, charger)

    process.terminate.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)
    process.kill.assert_not_called()
    assert not session_path(charger).exists()
    assert not socket_path(charger).exists()
    assert not error_path(charger).exists()


def test_reconnect_waits_for_inflight_heartbeat():
    async def exercise():
        fake = FakeSimulator()
        fake.heartbeat_started = asyncio.Event()
        fake.release_heartbeat = asyncio.Event()
        worker = worker_with(fake)
        await worker.connect_and_boot()

        heartbeat_task = asyncio.create_task(worker.heartbeat())
        await fake.heartbeat_started.wait()
        reconnect_task = asyncio.create_task(worker.dispatch({"action": "reconnect"}))

        await asyncio.sleep(0)
        assert fake.reconnects == 0

        fake.release_heartbeat.set()
        await heartbeat_task
        response = await reconnect_task

        assert fake.calls == [("Heartbeat", {})]
        assert fake.reconnects == 1
        assert response["reconnects"] == 1

    asyncio.run(exercise())


def test_worker_authorization_scenario_returns_privacy_safe_matrix():
    class MatrixSimulator(FakeSimulator):
        def __init__(self):
            super().__init__()
            self.outcomes = iter(["Accepted", "Blocked", "Invalid", "Accepted"])

        async def authorize(self, id_tag):
            self.calls.append(("Authorize", id_tag))
            return next(self.outcomes)

    async def exercise():
        fake = MatrixSimulator()
        worker = worker_with(fake)
        await worker.connect_and_boot()

        response = await worker.dispatch(
            {
                "action": "authorize-scenario",
                "policy_context": "restricted",
                "known_authorized": "KNOWN-OK",
                "known_denied": "KNOWN-NO",
                "unknown": "UNKNOWN",
            }
        )

        assert response["scenario"] == "restricted-authorization-matrix"
        assert response["policy_context"] == "restricted"
        assert [item["status"] for item in response["results"]] == [
            "Accepted",
            "Blocked",
            "Invalid",
            "Accepted",
        ]
        assert [item["attempt"] for item in response["results"]] == [
            "known-authorized",
            "known-denied",
            "unknown",
            "known-authorized-repeat",
        ]
        assert all("id_tag" not in item for item in response["results"])
        assert fake.calls == [
            ("Authorize", "KNOWN-OK"),
            ("Authorize", "KNOWN-NO"),
            ("Authorize", "UNKNOWN"),
            ("Authorize", "KNOWN-OK"),
        ]

    asyncio.run(exercise())


def test_authorize_scenario_command_passes_operator_matrix_to_worker(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("OCPP_SIMULATOR_RUNTIME_DIR", str(tmp_path))

    async def fake_send_control(charger, request):
        assert charger == "GWAY001"
        assert request == {
            "action": "authorize-scenario",
            "policy_context": "open",
            "known_authorized": "KNOWN-OK",
            "known_denied": "KNOWN-NO",
            "unknown": "UNKNOWN",
        }
        return {
            "ok": True,
            "charger": charger,
            "scenario": "open-authorization-matrix",
            "policy_context": "open",
            "results": [],
        }

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.send_control",
        fake_send_control,
    )

    call_command(
        "ocpp_simulator",
        "authorize-scenario",
        "--charger",
        "GWAY001",
        "--policy-context",
        "open",
        "--known-authorized",
        "KNOWN-OK",
        "--known-denied",
        "KNOWN-NO",
        "--unknown",
        "UNKNOWN",
    )

    output = capsys.readouterr().out
    assert "Authorization scenario: open-authorization-matrix" in output
    assert "Policy context: open" in output


def test_authorize_scenario_json_output_is_machine_readable(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("OCPP_SIMULATOR_RUNTIME_DIR", str(tmp_path))

    async def fake_send_control(charger, request):
        return {
            "ok": True,
            "charger": charger,
            "scenario": "restricted-authorization-matrix",
            "policy_context": "restricted",
            "results": [
                {
                    "scenario": "restricted-authorization-matrix",
                    "attempt": "known-authorized",
                    "sequence": 1,
                    "status": "Accepted",
                    "error": None,
                    "repeat_of": None,
                    "policy_context": "restricted",
                }
            ],
        }

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.send_control",
        fake_send_control,
    )

    call_command(
        "ocpp_simulator",
        "authorize-scenario",
        "--charger",
        "GWAY001",
        "--policy-context",
        "restricted",
        "--known-authorized",
        "KNOWN-OK",
        "--known-denied",
        "KNOWN-NO",
        "--unknown",
        "UNKNOWN",
        "--json",
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["scenario"] == "restricted-authorization-matrix"
    assert payload["results"][0]["status"] == "Accepted"
    assert "id_tag" not in payload["results"][0]


def test_authorization_scenario_human_output_shows_errors_and_repeats():
    output = Command._format_authorization_scenario(
        {
            "charger": "GWAY001",
            "scenario": "restricted-authorization-matrix",
            "policy_context": "restricted",
            "results": [
                {
                    "sequence": 1,
                    "attempt": "known-authorized",
                    "status": "Accepted",
                    "error": None,
                    "repeat_of": None,
                },
                {
                    "sequence": 2,
                    "attempt": "known-authorized-repeat",
                    "status": None,
                    "error": "connection receive failed",
                    "repeat_of": "known-authorized",
                },
            ],
        }
    )

    assert "1. known-authorized: Accepted" in output
    assert (
        "2. known-authorized-repeat: ERROR: connection receive failed "
        "(repeat of known-authorized)"
    ) in output
    assert "KNOWN-OK" not in output

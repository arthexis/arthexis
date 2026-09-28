import json
from unittest.mock import Mock

import pytest
from django.core.management import CommandError, call_command

from apps.ocpp.management.commands.ocpp_simulator import Command
from apps.ocpp.simulator.worker import error_path, session_path, socket_path


def scenario_args(*, policy_context="open", json_output=False):
    args = [
        "ocpp_simulator",
        "authorize-scenario",
        "--charger",
        "GWAY001",
        "--policy-context",
        policy_context,
        "--known-authorized",
        "KNOWN-OK",
        "--known-denied",
        "KNOWN-NO",
        "--unknown",
        "UNKNOWN",
    ]
    if json_output:
        args.append("--json")
    return args


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


def test_authorize_scenario_passes_operator_matrix_and_formats_human_output(
    monkeypatch, capsys
):
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

    call_command(*scenario_args())

    output = capsys.readouterr().out
    assert "Authorization scenario: open-authorization-matrix" in output
    assert "Policy context: open" in output


def test_authorize_scenario_json_output_is_machine_readable(monkeypatch, capsys):
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

    call_command(*scenario_args(policy_context="restricted", json_output=True))

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


def test_replay_command_forwards_source_pacing_and_reconnect(monkeypatch, capsys):
    async def fake_send_control(charger, request):
        assert charger == "GWAY001"
        assert request == {
            "action": "replay",
            "source": "/tmp/reconciled.sqlite3",
            "source_charger": "field-charger",
            "stream": "transactions",
            "batch_size": 64,
            "pacing": "fixed",
            "interval_seconds": 0.5,
            "burst_size": 100,
            "burst_pause_seconds": 0.0,
            "reconnect_after": 20,
        }
        return {
            "ok": True,
            "charger": charger,
            "source_kind": "database",
            "capture_id": None,
            "source_charger": "field-charger",
            "stream": "transactions",
            "events_completed": 3,
            "actions": ["StartTransaction", "MeterValues", "StopTransaction"],
            "pacing": "fixed",
            "reconnect_after": 20,
        }

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.send_control",
        fake_send_control,
    )

    call_command(
        "ocpp_simulator",
        "replay",
        "--charger",
        "GWAY001",
        "--source",
        "/tmp/reconciled.sqlite3",
        "--source-charger",
        "field-charger",
        "--batch-size",
        "64",
        "--pacing",
        "fixed",
        "--interval-seconds",
        "0.5",
        "--reconnect-after",
        "20",
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["events_completed"] == 3
    assert payload["source_kind"] == "database"


def test_replay_command_selects_inbound_stream(monkeypatch, capsys):
    async def fake_send_control(charger, request):
        assert request["stream"] == "inbound"
        return {
            "ok": True,
            "charger": charger,
            "source_kind": "database",
            "capture_id": None,
            "source_charger": None,
            "stream": "inbound",
            "events_completed": 1,
            "actions": ["Authorize"],
            "pacing": "maximum",
            "reconnect_after": None,
        }

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.send_control",
        fake_send_control,
    )

    call_command(
        "ocpp_simulator",
        "replay",
        "--charger",
        "GWAY001",
        "--source",
        "/tmp/reconciled.sqlite3",
        "--stream",
        "inbound",
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["stream"] == "inbound"
    assert payload["actions"] == ["Authorize"]

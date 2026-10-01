import json

import pytest
from django.core.management import CommandError, call_command

from apps.ocpp.management.commands.ocpp_simulator import Command


def replay_args(*, stream="transactions"):
    args = [
        "ocpp_simulator",
        "replay",
        "--charger",
        "GWAY001",
        "--source",
        "/tmp/reconciled.sqlite3",
    ]
    if stream != "transactions":
        args.extend(["--stream", stream])
    return args


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
        *replay_args(),
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

    call_command(*replay_args(stream="inbound"))

    payload = json.loads(capsys.readouterr().out)
    assert payload["stream"] == "inbound"
    assert payload["actions"] == ["Authorize"]


def test_start_uses_endpoint_and_local_identity(monkeypatch):
    seen = {}

    def fake_open(self, options):
        seen.update(options)

    monkeypatch.setenv("ARTHEXIS_OCPP_SIMULATOR_IDENTITY", "GW001")
    monkeypatch.setattr(Command, "_open", fake_open)

    call_command("ocpp_simulator", "start", "ws://192.168.129.10:9000")

    assert seen["action"] == "start"
    assert seen["endpoint"] == "ws://192.168.129.10:9000"
    assert seen["charger"] is None


def test_default_identity_prefers_explicit_environment(monkeypatch):
    monkeypatch.setenv("ARTHEXIS_OCPP_SIMULATOR_IDENTITY", "GW001")

    assert Command._default_charger_identity() == "GW001"


@pytest.mark.parametrize(
    ("endpoint", "allowed"),
    [
        ("ws://192.168.129.10:9000", True),
        ("ws://127.0.0.1:9000", True),
        ("ws://169.254.10.2:9000", True),
        ("ws://example.test:9000", False),
        ("wss://192.168.129.10:9000", False),
    ],
)
def test_local_plaintext_policy_only_auto_allows_local_ip_endpoints(endpoint, allowed):
    assert Command._allow_local_insecure_ws(endpoint) is allowed


def test_session_authorize_uses_default_identity_and_positional_tag(monkeypatch, capsys):
    seen = {}

    async def fake_send_control(charger, request):
        seen["charger"] = charger
        seen["request"] = request
        return {"ok": True, "charger": charger, "authorization": "Accepted"}

    monkeypatch.setenv("ARTHEXIS_OCPP_SIMULATOR_IDENTITY", "GW001")
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.send_control",
        fake_send_control,
    )

    call_command("ocpp_simulator", "authorize", "TEST001")

    assert seen == {
        "charger": "GW001",
        "request": {"action": "authorize", "id_tag": "TEST001"},
    }
    assert json.loads(capsys.readouterr().out)["authorization"] == "Accepted"


def test_session_replay_uses_default_identity_and_positional_source(monkeypatch, capsys):
    seen = {}

    async def fake_send_control(charger, request):
        seen["charger"] = charger
        seen["request"] = request
        return {
            "ok": True,
            "charger": charger,
            "source_kind": "database",
            "source_charger": None,
            "stream": "transactions",
            "events_completed": 0,
            "actions": [],
            "actions_truncated": False,
            "pacing": "maximum",
            "reconnect_after": None,
            "metrics": {},
        }

    monkeypatch.setenv("ARTHEXIS_OCPP_SIMULATOR_IDENTITY", "GW001")
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.send_control",
        fake_send_control,
    )

    call_command("ocpp_simulator", "replay", "reconciled.sqlite3")

    assert seen["charger"] == "GW001"
    assert seen["request"]["source"] == "reconciled.sqlite3"
    assert seen["request"]["pacing"] == "maximum"
    assert json.loads(capsys.readouterr().out)["events_completed"] == 0

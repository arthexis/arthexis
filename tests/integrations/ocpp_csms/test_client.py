from __future__ import annotations

import json
import subprocess

import pytest

from integrations.ocpp_csms.client import (
    LocalCliClient,
    OcppCsmsCommandError,
    OcppCsmsContractError,
    OcppCsmsTimeout,
    OcppCsmsUnavailable,
    SCHEMAS,
)


def completed(schema: str, data: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps({"schema": schema, "data": data or {}}),
        stderr="",
    )


def test_status_reads_data_and_builds_local_command(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return completed(SCHEMAS["status"], {"chargers": []})

    monkeypatch.setattr("integrations.ocpp_csms.client.subprocess.run", run)

    client = LocalCliClient(data_dir="/srv/ocpp", timeout=2.5)
    assert client.status("CP001", charging=True) == {"chargers": []}
    assert calls == [
        (
            [
                "ocpp-csms",
                "--data-dir",
                "/srv/ocpp",
                "status",
                "CP001",
                "--charging",
                "--json",
            ],
            {
                "capture_output": True,
                "text": True,
                "timeout": 2.5,
                "check": False,
            },
        )
    ]


@pytest.mark.parametrize(
    ("method", "schema", "expected"),
    [
        (
            lambda client: client.transactions(
                active=True,
                charger="CP001",
                connector=1,
                id_tag="CARD-1",
                since="2026-10-01T00:00:00Z",
                until="2026-10-02T00:00:00Z",
                limit=5,
            ),
            "transactions",
            [
                "ocpp-csms",
                "transactions",
                "--active",
                "--charger",
                "CP001",
                "--connector",
                "1",
                "--id-tag",
                "CARD-1",
                "--since",
                "2026-10-01T00:00:00Z",
                "--until",
                "2026-10-02T00:00:00Z",
                "--limit",
                "5",
                "--json",
            ],
        ),
        (
            lambda client: client.energy(
                charger="CP001",
                connector=1,
                since="2026-10-01T00:00:00Z",
                until="2026-10-02T00:00:00Z",
            ),
            "energy",
            [
                "ocpp-csms",
                "energy",
                "--charger",
                "CP001",
                "--connector",
                "1",
                "--since",
                "2026-10-01T00:00:00Z",
                "--until",
                "2026-10-02T00:00:00Z",
                "--json",
            ],
        ),
        (
            lambda client: client.events(
                "CP001",
                transaction=42,
                since="2026-10-01T00:00:00Z",
                until="2026-10-02T00:00:00Z",
                limit=10,
            ),
            "events",
            [
                "ocpp-csms",
                "events",
                "CP001",
                "--transaction",
                "42",
                "--since",
                "2026-10-01T00:00:00Z",
                "--until",
                "2026-10-02T00:00:00Z",
                "--limit",
                "10",
                "--json",
            ],
        ),
    ],
)
def test_read_methods_use_stable_contracts(monkeypatch, method, schema, expected):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return completed(SCHEMAS[schema], {"ok": True})

    monkeypatch.setattr("integrations.ocpp_csms.client.subprocess.run", run)

    assert method(LocalCliClient()) == {"ok": True}
    assert calls == [expected]


def test_transaction_detail_is_supported(monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return completed(SCHEMAS["transactions"], {"transactions": []})

    monkeypatch.setattr("integrations.ocpp_csms.client.subprocess.run", run)

    LocalCliClient().transactions(42)
    assert commands == [["ocpp-csms", "transactions", "42", "--json"]]


def test_transactions_rejects_conflicting_selectors():
    with pytest.raises(ValueError, match="mutually exclusive"):
        LocalCliClient().transactions(active=True, last=True)


def test_nonzero_exit_raises_command_error(monkeypatch):
    monkeypatch.setattr(
        "integrations.ocpp_csms.client.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=2,
            stdout="",
            stderr="bad filter\n",
        ),
    )

    with pytest.raises(OcppCsmsCommandError, match="exit code 2") as exc:
        LocalCliClient().status()

    assert exc.value.returncode == 2
    assert exc.value.stderr == "bad filter\n"


def test_missing_command_raises_unavailable(monkeypatch):
    def run(*args, **kwargs):
        raise FileNotFoundError("ocpp-csms")

    monkeypatch.setattr("integrations.ocpp_csms.client.subprocess.run", run)

    with pytest.raises(OcppCsmsUnavailable, match="command not found"):
        LocalCliClient().status()


def test_timeout_raises_integration_timeout(monkeypatch):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr("integrations.ocpp_csms.client.subprocess.run", run)

    with pytest.raises(OcppCsmsTimeout, match="5 seconds"):
        LocalCliClient().status()


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        ("not-json", "invalid JSON"),
        (json.dumps([]), "envelope must be an object"),
        (json.dumps({"schema": SCHEMAS["status"], "data": []}), "data must be an object"),
    ],
)
def test_invalid_contract_shape_is_rejected(monkeypatch, stdout, message):
    monkeypatch.setattr(
        "integrations.ocpp_csms.client.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        ),
    )

    with pytest.raises(OcppCsmsContractError, match=message):
        LocalCliClient().status()


def test_unexpected_schema_version_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "integrations.ocpp_csms.client.subprocess.run",
        lambda *args, **kwargs: completed("ocpp-csms/status/v2"),
    )

    with pytest.raises(OcppCsmsContractError, match="status/v1"):
        LocalCliClient().status()

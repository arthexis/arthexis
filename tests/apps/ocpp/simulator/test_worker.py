import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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


def test_worker_authorize_reuses_live_connection():
    worker = LiveSimulatorWorker(
        insecure_config(url="ws://example.test", charger="GWAY001")
    )
    worker._boot = SimpleNamespace(status="Accepted", interval=60)
    worker._simulator.authorize = AsyncMock(return_value="Invalid")

    response = asyncio.run(
        worker._dispatch({"action": "authorize", "id_tag": "UNKNOWN001"})
    )

    assert response["authorization"] == "Invalid"
    worker._simulator.authorize.assert_awaited_once_with("UNKNOWN001")


def test_worker_reconnect_reboots_same_charger():
    worker = LiveSimulatorWorker(
        insecure_config(url="ws://example.test", charger="GWAY001")
    )
    worker._boot = SimpleNamespace(status="Accepted", interval=60)
    worker._simulator.reconnect = AsyncMock()
    worker._simulator.boot = AsyncMock(
        return_value=SimpleNamespace(status="Accepted", interval=60)
    )

    response = asyncio.run(worker._dispatch({"action": "reconnect"}))

    assert response["reconnects"] == 1
    worker._simulator.reconnect.assert_awaited_once_with()
    worker._simulator.boot.assert_awaited_once_with()


def test_worker_close_requests_shutdown():
    worker = LiveSimulatorWorker(
        insecure_config(url="ws://example.test", charger="GWAY001")
    )
    response = asyncio.run(worker._dispatch({"action": "close"}))
    assert response["closed"] is True
    assert worker._stop.is_set()


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

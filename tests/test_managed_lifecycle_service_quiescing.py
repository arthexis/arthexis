from __future__ import annotations

import subprocess
from pathlib import Path

from apps.core.system import lifecycle


MANAGED_UNITS = {
    "gway-arthexis-web-local.service",
    "gway-arthexis-worker.service",
    "gway-arthexis-beat.service",
}


def _systemctl_action(command: list[str]) -> tuple[str, set[str]] | None:
    try:
        index = command.index("systemctl")
    except ValueError:
        return None
    if len(command) <= index + 1:
        return None
    return command[index + 1], set(command[index + 2 :])


def test_repeated_managed_install_quiesces_services_around_prepare(
    tmp_path: Path, monkeypatch
) -> None:
    """A second install must not migrate while existing services can write SQLite."""
    root = tmp_path / "arthexis"
    checkout = root / "app"
    checkout.mkdir(parents=True)
    managed = lifecycle.InstallationLayout(root=root, checkout=checkout)

    commands: list[list[str]] = []

    def fake_run(arguments, *args, **kwargs):
        command = [str(value) for value in arguments]
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(lifecycle.subprocess, "run", fake_run)
    monkeypatch.setattr(lifecycle, "_warn_if_local_redis_missing", lambda role: None)
    monkeypatch.setattr(lifecycle, "_record_managed_ownership", lambda current: None)
    monkeypatch.setattr(lifecycle, "_restore_runtime_ownership", lambda state_root: None)

    # The first install represents the deployment that creates and starts the
    # managed services. Clear the command trace so the assertion describes the
    # contract required of a subsequent idempotency install.
    lifecycle.install("--role", "Watchtower", layout=managed)
    commands.clear()

    lifecycle.install("--role", "Watchtower", layout=managed)

    manage_indexes = [
        index
        for index, command in enumerate(commands)
        if "manage.py" in command
    ]
    assert manage_indexes, "managed install did not run application preparation"

    systemctl = [
        (index, parsed)
        for index, command in enumerate(commands)
        if (parsed := _systemctl_action(command)) is not None
    ]
    stop_indexes = [
        index
        for index, (action, units) in systemctl
        if action == "stop" and MANAGED_UNITS <= units
    ]
    start_indexes = [
        index
        for index, (action, units) in systemctl
        if action in {"start", "restart"} and MANAGED_UNITS <= units
    ]

    assert stop_indexes, "managed services were not quiesced before preparation"
    assert max(stop_indexes) < min(manage_indexes)
    assert start_indexes, "managed services were not restored after preparation"
    assert min(start_indexes) > max(manage_indexes)

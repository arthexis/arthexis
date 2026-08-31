"""Tests for USB stability helpers used during SD-card writes."""

from __future__ import annotations

import subprocess
import threading

from apps.imager import usb_stability


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_quiet_usb_pollers_stops_and_restores_active_units(monkeypatch) -> None:
    commands: list[list[str]] = []
    system_states = {
        "arthexis-usb-inventory.service": "active",
        "arthexis-usb-inventory.timer": "active",
        "bastion-usb-refresh.timer": "inactive",
        "bastion-usb-refresh.service": "active",
        "kindle-postbox.service": "active",
        "udisks2.service": "active",
    }
    user_states = {
        "gvfs-udisks2-volume-monitor.service": "active",
        "gvfs-mtp-volume-monitor.service": "inactive",
        "gvfs-gphoto2-volume-monitor.service": "inactive",
        "gvfs-afc-volume-monitor.service": "inactive",
    }

    monkeypatch.setattr(usb_stability.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(usb_stability.os, "geteuid", lambda: 1000)

    def runner(command, **_kwargs):
        command = list(command)
        commands.append(command)
        if command == ["sudo", "-n", "true"]:
            return _completed()
        if command[:2] == ["systemctl", "is-active"]:
            return _completed(system_states.get(command[2], "inactive"))
        if command[:3] == ["systemctl", "--user", "is-active"]:
            return _completed(user_states.get(command[3], "inactive"))
        if command[:4] == ["sudo", "-n", "systemctl", "stop"]:
            system_states[command[4]] = "inactive"
            return _completed()
        if command[:4] == ["sudo", "-n", "systemctl", "start"]:
            system_states[command[4]] = "active"
            return _completed()
        if command[:3] == ["systemctl", "--user", "stop"]:
            user_states[command[3]] = "inactive"
            return _completed()
        if command[:3] == ["systemctl", "--user", "start"]:
            user_states[command[3]] = "active"
            return _completed()
        if command[:4] == ["sudo", "-n", "test", "-e"]:
            return _completed(returncode=1)
        return _completed()

    messages: list[str] = []
    with usb_stability.quiet_usb_pollers(log=messages.append, runner=runner) as session:
        assert session.enabled is True
        assert system_states["arthexis-usb-inventory.service"] == "inactive"
        assert system_states["arthexis-usb-inventory.timer"] == "inactive"
        assert system_states["bastion-usb-refresh.service"] == "inactive"
        assert system_states["kindle-postbox.service"] == "inactive"
        assert user_states["gvfs-udisks2-volume-monitor.service"] == "inactive"

    assert system_states["arthexis-usb-inventory.service"] == "active"
    assert system_states["arthexis-usb-inventory.timer"] == "active"
    assert system_states["bastion-usb-refresh.service"] == "active"
    assert system_states["kindle-postbox.service"] == "active"
    assert user_states["gvfs-udisks2-volume-monitor.service"] == "active"
    assert ["sudo", "-n", "systemctl", "stop", "arthexis-usb-inventory.service"] in commands
    assert ["sudo", "-n", "systemctl", "stop", "arthexis-usb-inventory.timer"] in commands
    assert ["sudo", "-n", "systemctl", "stop", "bastion-usb-refresh.service"] in commands
    assert ["sudo", "-n", "systemctl", "stop", "kindle-postbox.service"] in commands
    assert ["systemctl", "--user", "stop", "gvfs-udisks2-volume-monitor.service"] in commands
    assert ["sudo", "-n", "systemctl", "start", "bastion-usb-refresh.service"] in commands
    assert ["sudo", "-n", "systemctl", "start", "kindle-postbox.service"] in commands
    assert ["sudo", "-n", "rm", "-f", str(usb_stability.BASTION_USB_REFRESH_HOLD)] in commands
    assert messages[0].startswith("quiet-usb: pausing")
    assert messages[-1].startswith("quiet-usb: restoring")


def test_quiet_usb_pollers_honors_disabled_environment(monkeypatch) -> None:
    monkeypatch.setenv(usb_stability.QUIET_USB_ENV, "0")

    def runner(*_args, **_kwargs):
        raise AssertionError("quiet USB should be disabled")

    with usb_stability.quiet_usb_pollers(runner=runner) as session:
        assert session.enabled is False


def test_exclusive_quiet_usb_window_serializes_threads(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARTHEXIS_QUIET_USB_LOCK", str(tmp_path / "quiet-usb.lock"))
    session = usb_stability.QuietUsbSession(enabled=True)
    order: list[str] = []
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    errors: list[BaseException] = []

    def first_window() -> None:
        try:
            with usb_stability._exclusive_quiet_usb_window(session, log=None):
                order.append("first-enter")
                first_entered.set()
                assert not second_entered.wait(timeout=0.05)
                release_first.wait(timeout=1)
                order.append("first-exit")
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    def second_window() -> None:
        try:
            assert first_entered.wait(timeout=1)
            with usb_stability._exclusive_quiet_usb_window(session, log=None):
                order.append("second-enter")
                second_entered.set()
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    first = threading.Thread(target=first_window)
    second = threading.Thread(target=second_window)
    first.start()
    second.start()
    assert first_entered.wait(timeout=1)
    assert not second_entered.wait(timeout=0.05)
    release_first.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert order == ["first-enter", "first-exit", "second-enter"]


def test_quiet_usb_pollers_yields_when_setup_step_raises(monkeypatch) -> None:
    calls: list[str] = []
    messages: list[str] = []

    monkeypatch.setattr(usb_stability, "_systemctl_available", lambda **_kwargs: True)

    def install_hold(*_args, **_kwargs) -> None:
        calls.append("install-hold")
        raise RuntimeError("setup failed")

    monkeypatch.setattr(usb_stability, "_install_bastion_refresh_hold", install_hold)
    monkeypatch.setattr(
        usb_stability,
        "_pause_system_units",
        lambda *_args, **_kwargs: calls.append("pause-system"),
    )
    monkeypatch.setattr(
        usb_stability,
        "_pause_user_units",
        lambda *_args, **_kwargs: calls.append("pause-user"),
    )
    monkeypatch.setattr(
        usb_stability,
        "_restore_user_units",
        lambda *_args, **_kwargs: calls.append("restore-user"),
    )
    monkeypatch.setattr(
        usb_stability,
        "_restore_system_units",
        lambda *_args, **_kwargs: calls.append("restore-system"),
    )
    monkeypatch.setattr(
        usb_stability,
        "_release_bastion_refresh_hold",
        lambda *_args, **_kwargs: calls.append("release-hold"),
    )

    with usb_stability.quiet_usb_pollers(log=messages.append):
        calls.append("burn")

    assert calls == [
        "install-hold",
        "pause-system",
        "pause-user",
        "burn",
        "restore-user",
        "restore-system",
        "release-hold",
    ]
    assert any("failed to install bastion refresh hold" in message for message in messages)


def test_quiet_usb_pollers_attempts_all_restore_steps_after_failure(
    monkeypatch,
) -> None:
    calls: list[str] = []
    messages: list[str] = []

    monkeypatch.setattr(usb_stability, "_systemctl_available", lambda **_kwargs: True)
    monkeypatch.setattr(
        usb_stability,
        "_install_bastion_refresh_hold",
        lambda *_args, **_kwargs: calls.append("install-hold"),
    )
    monkeypatch.setattr(
        usb_stability,
        "_pause_system_units",
        lambda *_args, **_kwargs: calls.append("pause-system"),
    )
    monkeypatch.setattr(
        usb_stability,
        "_pause_user_units",
        lambda *_args, **_kwargs: calls.append("pause-user"),
    )

    def restore_user(*_args, **_kwargs) -> None:
        calls.append("restore-user")
        raise RuntimeError("user restore failed")

    monkeypatch.setattr(usb_stability, "_restore_user_units", restore_user)
    monkeypatch.setattr(
        usb_stability,
        "_restore_system_units",
        lambda *_args, **_kwargs: calls.append("restore-system"),
    )
    monkeypatch.setattr(
        usb_stability,
        "_release_bastion_refresh_hold",
        lambda *_args, **_kwargs: calls.append("release-hold"),
    )

    with usb_stability.quiet_usb_pollers(log=messages.append):
        calls.append("burn")

    assert calls == [
        "install-hold",
        "pause-system",
        "pause-user",
        "burn",
        "restore-user",
        "restore-system",
        "release-hold",
    ]
    assert any("failed to restore user units" in message for message in messages)


def test_install_bastion_refresh_hold_skips_existing_hold_touch(monkeypatch) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr(usb_stability.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(usb_stability.os, "geteuid", lambda: 1000)

    def runner(command, **_kwargs):
        command = list(command)
        commands.append(command)
        if command == ["sudo", "-n", "true"]:
            return _completed()
        if command[:4] == ["sudo", "-n", "test", "-e"]:
            return _completed(returncode=0)
        return _completed()

    session = usb_stability.QuietUsbSession(enabled=True)

    usb_stability._install_bastion_refresh_hold(
        session,
        runner=runner,
        log=None,
    )

    assert session.bastion_hold_prior_state == "present"
    assert session.bastion_hold_token_path is not None
    assert [
        "sudo",
        "-n",
        "touch",
        str(usb_stability.BASTION_USB_REFRESH_HOLD),
    ] not in commands
    assert [
        "sudo",
        "-n",
        "install",
        "-d",
        "-m",
        "0755",
        str(usb_stability.BASTION_USB_REFRESH_HOLD.parent),
    ] not in commands


def test_install_bastion_refresh_hold_marks_existing_owned_hold_as_managed(
    monkeypatch,
) -> None:
    commands: list[list[str]] = []
    other_token_path = usb_stability.BASTION_USB_REFRESH_HOLD_TOKENS / "other.hold"

    monkeypatch.setattr(usb_stability.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(usb_stability.os, "geteuid", lambda: 1000)

    def runner(command, **_kwargs):
        command = list(command)
        commands.append(command)
        if command == ["sudo", "-n", "true"]:
            return _completed()
        if command[:4] == ["sudo", "-n", "test", "-e"]:
            return _completed(returncode=0)
        if command[:3] == ["sudo", "-n", "find"]:
            return _completed(f"{other_token_path}\n")
        return _completed()

    session = usb_stability.QuietUsbSession(enabled=True)

    usb_stability._install_bastion_refresh_hold(
        session,
        runner=runner,
        log=None,
    )

    assert session.bastion_hold_prior_state == "managed"
    assert session.bastion_hold_token_path is not None
    assert [
        "sudo",
        "-n",
        "touch",
        str(usb_stability.BASTION_USB_REFRESH_HOLD),
    ] not in commands


def test_release_bastion_refresh_hold_keeps_shared_hold_with_other_token(
    monkeypatch,
) -> None:
    commands: list[list[str]] = []
    token_path = usb_stability.BASTION_USB_REFRESH_HOLD_TOKENS / "first.hold"
    other_token_path = usb_stability.BASTION_USB_REFRESH_HOLD_TOKENS / "second.hold"
    session = usb_stability.QuietUsbSession(
        enabled=True,
        bastion_hold_prior_state="absent",
        bastion_hold_token_path=token_path,
    )

    monkeypatch.setattr(usb_stability.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(usb_stability.os, "geteuid", lambda: 1000)

    def runner(command, **_kwargs):
        command = list(command)
        commands.append(command)
        if command == ["sudo", "-n", "true"]:
            return _completed()
        if command[:3] == ["sudo", "-n", "find"]:
            return _completed(f"{other_token_path}\n")
        return _completed()

    usb_stability._release_bastion_refresh_hold(session, runner=runner, log=None)

    assert ["sudo", "-n", "rm", "-f", str(token_path)] in commands
    assert [
        "sudo",
        "-n",
        "rm",
        "-f",
        str(usb_stability.BASTION_USB_REFRESH_HOLD),
    ] not in commands


def test_release_bastion_refresh_hold_removes_managed_hold_after_last_token(
    monkeypatch,
) -> None:
    commands: list[list[str]] = []
    token_path = usb_stability.BASTION_USB_REFRESH_HOLD_TOKENS / "last.hold"
    session = usb_stability.QuietUsbSession(
        enabled=True,
        bastion_hold_prior_state="managed",
        bastion_hold_token_path=token_path,
    )

    monkeypatch.setattr(usb_stability.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(usb_stability.os, "geteuid", lambda: 1000)

    def runner(command, **_kwargs):
        command = list(command)
        commands.append(command)
        if command == ["sudo", "-n", "true"]:
            return _completed()
        if command[:3] == ["sudo", "-n", "find"]:
            return _completed("")
        return _completed()

    usb_stability._release_bastion_refresh_hold(session, runner=runner, log=None)

    assert ["sudo", "-n", "rm", "-f", str(token_path)] in commands
    assert [
        "sudo",
        "-n",
        "rm",
        "-f",
        str(usb_stability.BASTION_USB_REFRESH_HOLD),
    ] in commands

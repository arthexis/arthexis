from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def _enable_control_peripheral_apps(settings) -> None:
    settings.NODE_ROLE = "Control"
    installed_apps = list(settings.INSTALLED_APPS)
    for app_selector in ("apps.cards",):
        if app_selector not in installed_apps:
            installed_apps.append(app_selector)
    settings.INSTALLED_APPS = installed_apps


def test_doctor_defaults_to_core_group(monkeypatch: pytest.MonkeyPatch) -> None:
    invoked: list[tuple[str, ...]] = []

    def _fake_call_command(*args, **kwargs):
        del kwargs
        invoked.append(tuple(args))

    monkeypatch.setattr(
        "apps.core.management.commands.doctor.call_command", _fake_call_command
    )

    call_command("doctor")

    assert invoked == [
        ("good", "--details"),
        ("health", "--group", "core"),
        ("migrations", "check"),
    ]


def test_doctor_runs_selected_peripheral_target(
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    _enable_control_peripheral_apps(settings)
    invoked: list[tuple[str, ...]] = []

    def _fake_call_command(*args, **kwargs):
        del kwargs
        invoked.append(tuple(args))

    monkeypatch.setattr(
        "apps.core.management.commands.doctor.call_command", _fake_call_command
    )

    call_command("doctor", "--target", "cards.rfid")

    assert invoked == [("rfid", "doctor")]


def test_doctor_rejects_unknown_selector() -> None:
    with pytest.raises(CommandError, match="Unknown doctor target/group selector"):
        call_command("doctor", "--target", "missing.value")


def test_doctor_passes_force_to_core_health(monkeypatch: pytest.MonkeyPatch) -> None:
    invoked: list[tuple[str, ...]] = []

    def _fake_call_command(*args, **kwargs):
        del kwargs
        invoked.append(tuple(args))

    monkeypatch.setattr(
        "apps.core.management.commands.doctor.call_command", _fake_call_command
    )

    call_command("doctor", "--target", "core.health", "--force")

    assert invoked == [("health", "--group", "core", "--force")]


def test_doctor_lists_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.core.management.commands.doctor.call_command",
        lambda *args, **kwargs: None,
    )

    stdout = StringIO()
    call_command("doctor", "--list-targets", stdout=stdout)

    output = stdout.getvalue()
    assert "core.health" in output
    assert "cards.rfid" in output
    assert "video.camera" not in output


def test_doctor_runs_selected_group(
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    _enable_control_peripheral_apps(settings)
    invoked: list[tuple[str, ...]] = []

    def _fake_call_command(*args, **kwargs):
        del kwargs
        invoked.append(tuple(args))

    monkeypatch.setattr(
        "apps.core.management.commands.doctor.call_command", _fake_call_command
    )

    call_command("doctor", "--group", "peripherals")

    assert invoked == [("rfid", "doctor")]


def test_doctor_all_runs_all_targets_once(
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    _enable_control_peripheral_apps(settings)
    invoked: list[tuple[str, ...]] = []

    def _fake_call_command(*args, **kwargs):
        del kwargs
        invoked.append(tuple(args))

    monkeypatch.setattr(
        "apps.core.management.commands.doctor.call_command", _fake_call_command
    )

    call_command("doctor", "--all")

    assert invoked == [
        ("good", "--details"),
        ("health", "--group", "core"),
        ("migrations", "check"),
        ("rfid", "doctor"),
    ]


def test_doctor_skips_control_peripherals_on_non_control_role(
    monkeypatch: pytest.MonkeyPatch,
    settings,
    ) -> None:
    settings.NODE_ROLE = "Watchtower"
    settings.INSTALLED_APPS = ["apps.core", "apps.cards"]
    invoked: list[tuple[str, ...]] = []

    def _fake_call_command(*args, **kwargs):
        del kwargs
        invoked.append(tuple(args))

    monkeypatch.setattr(
        "apps.core.management.commands.doctor.call_command",
        _fake_call_command,
    )

    stdout = StringIO()
    call_command("doctor", "--group", "peripherals", stdout=stdout)

    assert invoked == []
    output = stdout.getvalue()
    assert (
        "[skipped] cards.rfid: node role Watchtower is not eligible; expected: Control"
        in output
    )
    assert "Doctor checks passed." in output


def test_doctor_rejects_retired_video_target(
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    settings.NODE_ROLE = "Control"
    settings.INSTALLED_APPS = ["apps.core"]
    monkeypatch.setattr(
        "apps.core.management.commands.doctor.call_command",
        lambda *args, **kwargs: None,
    )

    with pytest.raises(CommandError, match="video.camera"):
        call_command("doctor", "--target", "video.camera")

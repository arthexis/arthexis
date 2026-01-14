from __future__ import annotations

import json
import subprocess

from apps.core import uptime_utils


def test_ap_mode_enabled_returns_false_without_nmcli(monkeypatch):
    monkeypatch.setattr(uptime_utils.shutil, "which", lambda _: None)

    assert uptime_utils.ap_mode_enabled() is False


def test_ap_mode_enabled_handles_malformed_lines(monkeypatch):
    monkeypatch.setattr(uptime_utils.shutil, "which", lambda _: "/usr/bin/nmcli")

    results = iter(
        [
            subprocess.CompletedProcess(
                args=["nmcli"],
                returncode=0,
                stdout="wifi-ap:802-11-wireless\nbadline\nwifi-station:802-11-wireless\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["nmcli"],
                returncode=0,
                stdout="station\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["nmcli"],
                returncode=0,
                stdout="ap\n",
                stderr="",
            ),
        ]
    )

    def fake_run(*_args, **_kwargs):
        return next(results)

    monkeypatch.setattr(uptime_utils.subprocess, "run", fake_run)

    assert uptime_utils.ap_mode_enabled() is True


def test_ap_mode_enabled_returns_false_on_nmcli_error(monkeypatch):
    monkeypatch.setattr(uptime_utils.shutil, "which", lambda _: "/usr/bin/nmcli")

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["nmcli"],
            returncode=10,
            stdout="",
            stderr="error",
        )

    monkeypatch.setattr(uptime_utils.subprocess, "run", fake_run)

    assert uptime_utils.ap_mode_enabled() is False


def test_availability_seconds_prefers_duration_locks(tmp_path, monkeypatch):
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / uptime_utils.STARTUP_DURATION_LOCK_NAME).write_text(
        json.dumps({"duration_seconds": 60}),
        encoding="utf-8",
    )
    (lock_dir / uptime_utils.UPGRADE_DURATION_LOCK_NAME).write_text(
        json.dumps({"duration_seconds": 120}),
        encoding="utf-8",
    )
    called = {"boot_delay": False}

    def fake_boot_delay(*_args, **_kwargs):
        called["boot_delay"] = True
        return 999

    monkeypatch.setattr(uptime_utils, "boot_delay_seconds", fake_boot_delay)

    assert uptime_utils.availability_seconds(tmp_path, lambda *_args: None) == 120
    assert called["boot_delay"] is False


def test_availability_seconds_falls_back_to_boot_delay(tmp_path, monkeypatch):
    def fake_boot_delay(*_args, **_kwargs):
        return 42

    monkeypatch.setattr(uptime_utils, "boot_delay_seconds", fake_boot_delay)

    assert uptime_utils.availability_seconds(tmp_path, lambda *_args: None) == 42

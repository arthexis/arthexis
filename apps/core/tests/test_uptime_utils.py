from __future__ import annotations

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

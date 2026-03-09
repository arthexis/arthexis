"""Tests for the VS Code pytest launcher notification behavior."""

from __future__ import annotations

import pytest

from apps.vscode import test_server


class DummyProcess:
    """Simple process stub that returns a fixed exit code."""

    def __init__(self, return_code: int) -> None:
        self.return_code = return_code

    def wait(self) -> int:
        """Return the configured process exit code."""

        return self.return_code

    def terminate(self) -> None:
        """No-op termination hook for protocol compatibility."""

    def kill(self) -> None:
        """No-op kill hook for protocol compatibility."""


def test_run_tests_triggers_desktop_notification(monkeypatch) -> None:
    """run_tests should send a desktop notification after pytest completes."""

    monkeypatch.setattr(test_server.subprocess, "Popen", lambda *args, **kwargs: DummyProcess(0))
    calls: list[int] = []
    monkeypatch.setattr(test_server, "send_desktop_notification", lambda code: calls.append(code))

    return_code = test_server.run_tests(["-q"])

    assert return_code == 0
    assert calls == [0]


def test_send_desktop_notification_linux_uses_notify_send(monkeypatch) -> None:
    """Linux notifications should use notify-send when available."""

    monkeypatch.setattr(test_server.platform, "system", lambda: "Linux")
    monkeypatch.setattr(test_server.shutil, "which", lambda cmd: "/usr/bin/notify-send" if cmd == "notify-send" else None)
    recorded: list[list[str]] = []
    monkeypatch.setattr(test_server, "_run_notification_command", lambda cmd: recorded.append(cmd))

    test_server.send_desktop_notification(1)

    assert recorded == [["notify-send", "[Test Runner] Tests failed", "Pytest exited with code 1."]]


@pytest.mark.parametrize(
    ("system", "which_binary", "return_code", "expected_command"),
    [
        (
            "Darwin",
            None,
            0,
            [
                "osascript",
                "-e",
                'display notification "Pytest finished successfully." with title "[Test Runner] Tests passed"',
            ],
        ),
        (
            "Windows",
            "powershell",
            1,
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "$n = New-Object System.Windows.Forms.NotifyIcon; "
                    "$n.Icon = [System.Drawing.SystemIcons]::Information; "
                    "$n.Visible = $true; "
                    '$n.BalloonTipTitle = "[Test Runner] Tests failed"; '
                    '$n.BalloonTipText = "Pytest exited with code 1."; '
                    "$n.ShowBalloonTip(3000); Start-Sleep -Seconds 4; $n.Dispose();"
                ),
            ],
        ),
    ],
)
def test_send_desktop_notification_platform_commands(monkeypatch, system, which_binary, return_code, expected_command) -> None:
    """Supported platforms should construct the expected notification commands."""

    monkeypatch.setattr(test_server.platform, "system", lambda: system)
    monkeypatch.setattr(test_server.shutil, "which", lambda cmd: f"/usr/bin/{cmd}" if cmd == which_binary else None)
    recorded: list[list[str]] = []
    monkeypatch.setattr(test_server, "_run_notification_command", lambda cmd: recorded.append(cmd))

    test_server.send_desktop_notification(return_code)

    assert recorded == [expected_command]


def test_send_desktop_notification_escapes_quotes(monkeypatch) -> None:
    """Desktop notification commands should escape quote characters."""

    monkeypatch.setattr(test_server, "PREFIX", '[Test "Runner"]')
    recorded: list[list[str]] = []
    monkeypatch.setattr(test_server, "_run_notification_command", lambda cmd: recorded.append(cmd))

    monkeypatch.setattr(test_server.platform, "system", lambda: "Darwin")
    test_server.send_desktop_notification(1)
    assert '\\"Runner\\"' in recorded[0][2]

    recorded.clear()
    monkeypatch.setattr(test_server.platform, "system", lambda: "Windows")
    monkeypatch.setattr(test_server.shutil, "which", lambda cmd: "/usr/bin/powershell" if cmd == "powershell" else None)
    test_server.send_desktop_notification(1)
    assert '`"Runner`"' in recorded[0][3]


def test_send_desktop_notification_unsupported_platform_is_noop(monkeypatch) -> None:
    """Unsupported platforms should not attempt any notification commands."""

    monkeypatch.setattr(test_server.platform, "system", lambda: "Plan9")
    recorded: list[list[str]] = []
    monkeypatch.setattr(test_server, "_run_notification_command", lambda cmd: recorded.append(cmd))

    test_server.send_desktop_notification(0)

    assert recorded == []

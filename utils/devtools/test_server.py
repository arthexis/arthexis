#!/usr/bin/env python3
"""Run pytest once for developer launcher workflows."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Protocol, TypedDict

from utils.python_env import resolve_project_python

BASE_DIR = Path(__file__).resolve().parents[2]
PREFIX = "[Test Runner]"


class ProcessLike(Protocol):
    """Protocol for subprocess handles used by the developer test launcher."""

    def wait(self) -> int:
        """Wait for the process and return its exit code."""

    def terminate(self) -> None:
        """Request a graceful process termination."""

    def kill(self) -> None:
        """Forcefully terminate the process."""


class NotificationPayload(TypedDict):
    """Notification content derived from a completed pytest run."""

    title: str
    message: str
    status: str


def build_pytest_command(extra_args: list[str] | None = None) -> list[str]:
    """Build the command used to execute pytest.

    Args:
        extra_args: Optional extra CLI arguments forwarded to pytest.

    Returns:
        The subprocess command to execute.
    """

    command = [resolve_project_python(BASE_DIR), "-m", "pytest"]
    if extra_args:
        command.extend(extra_args)
    return command


def _build_subprocess_env() -> dict[str, str]:
    """Build subprocess environment with debug mode disabled for test runs.

    Returns:
        Environment variables for the pytest subprocess.
    """

    env = dict(os.environ)
    env["DEBUG"] = "0"
    env["DJANGO_DEBUG"] = "0"
    return env


def _build_notification_payload(return_code: int) -> NotificationPayload:
    """Build normalized notification content for a pytest result.

    Args:
        return_code: Exit code from pytest.

    Returns:
        Typed notification fields shared by platform-specific senders.
    """

    status = "passed" if return_code == 0 else "failed"
    return {
        "status": status,
        "title": f"{PREFIX} Tests {status}",
        "message": (
            "Pytest finished successfully."
            if return_code == 0
            else f"Pytest exited with code {return_code}."
        ),
    }


def _run_notification_command(command: list[str]) -> None:
    """Execute a desktop notification command in a best-effort manner.

    Args:
        command: Notification subprocess command.

    Returns:
        None.

    Raises:
        No exception is raised when the OS command is unavailable.
    """

    try:
        subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError:
        return


def send_desktop_notification(return_code: int) -> None:
    """Send a desktop notification when a test run completes, if supported.

    Args:
        return_code: Exit code from pytest.

    Returns:
        None.
    """

    system = platform.system()
    payload = _build_notification_payload(return_code)
    title = payload["title"]
    message = payload["message"]

    if system == "Linux":
        if shutil.which("notify-send"):
            _run_notification_command(["notify-send", title, message])
    elif system == "Darwin":
        safe_message = message.replace('"', '\\"')
        safe_title = title.replace('"', '\\"')
        script = f'display notification "{safe_message}" with title "{safe_title}"'
        _run_notification_command(["osascript", "-e", script])
    elif system == "Windows":
        if shutil.which("powershell"):
            safe_title = title.replace('"', '`"')
            safe_message = message.replace('"', '`"')
            command = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$n = New-Object System.Windows.Forms.NotifyIcon; "
                "$n.Icon = [System.Drawing.SystemIcons]::Information; "
                "$n.Visible = $true; "
                f'$n.BalloonTipTitle = "{safe_title}"; '
                f'$n.BalloonTipText = "{safe_message}"; '
                "$n.ShowBalloonTip(3000); Start-Sleep -Seconds 4; $n.Dispose();"
            )
            _run_notification_command(["powershell", "-NoProfile", "-Command", command])


def run_tests(extra_args: list[str] | None = None) -> int:
    """Run pytest and return the subprocess exit code.

    Args:
        extra_args: Optional extra CLI arguments forwarded to pytest.

    Returns:
        The pytest subprocess exit code.
    """

    command = build_pytest_command(extra_args)
    print(f"{PREFIX} Running: {' '.join(command)}")

    process: ProcessLike = subprocess.Popen(
        command, cwd=BASE_DIR, env=_build_subprocess_env()
    )
    try:
        return_code = process.wait()
    except KeyboardInterrupt:
        print(f"{PREFIX} Interrupted. Stopping pytest process...")
        process.terminate()
        try:
            process.wait()
        except KeyboardInterrupt:
            try:
                process.wait()
            except KeyboardInterrupt:
                return 130
            process.kill()
            try:
                process.wait()
            except KeyboardInterrupt:
                return 130
        return 130

    if return_code == 0:
        print(f"{PREFIX} Tests passed.")
    else:
        print(f"{PREFIX} Tests failed with exit code {return_code}.")

    send_desktop_notification(return_code)
    return return_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse launcher arguments for a one-shot pytest run.

    Args:
        argv: Optional CLI arguments to parse.

    Returns:
        Parsed command-line arguments.
    """

    parser = argparse.ArgumentParser(description="Run pytest once.")
    parser.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="Additional args passed to pytest.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for developer test launcher tasks.

    Args:
        argv: Optional CLI arguments.

    Returns:
        Process exit code for the pytest run.
    """

    args = parse_args(argv)
    extra_args = args.extra_args
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]
    return run_tests(extra_args)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())

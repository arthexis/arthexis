#!/usr/bin/env python3
"""Run pytest once for VS Code launcher workflows."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Protocol

BASE_DIR = Path(__file__).resolve().parents[2]
PREFIX = "[Test Runner]"


class ProcessLike(Protocol):
    """Protocol for subprocess handles used by the VS Code launcher."""

    def wait(self) -> int:
        """Wait for the process and return its exit code."""

    def terminate(self) -> None:
        """Request a graceful process termination."""

    def kill(self) -> None:
        """Forcefully terminate the process."""


def build_pytest_command(extra_args: list[str] | None = None) -> list[str]:
    """Build the command used to execute pytest."""

    command = [sys.executable, "-m", "pytest"]
    if extra_args:
        command.extend(extra_args)
    return command


def _build_subprocess_env() -> dict[str, str]:
    """Build subprocess environment with debug mode disabled for test runs."""

    env = dict(os.environ)
    env["DEBUG"] = "0"
    env["DJANGO_DEBUG"] = "0"
    return env


def _run_notification_command(command: list[str]) -> None:
    """Execute a desktop notification command in a best-effort manner."""

    try:
        subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError:
        # Notification support is optional in developer environments.
        return


def send_desktop_notification(return_code: int) -> None:
    """Send a desktop notification when a test run completes, if supported."""

    system = platform.system()
    status = "passed" if return_code == 0 else "failed"
    title = f"{PREFIX} Tests {status}"
    message = "Pytest finished successfully." if return_code == 0 else f"Pytest exited with code {return_code}."

    if system == "Linux":
        if shutil.which("notify-send"):
            _run_notification_command(["notify-send", title, message])
        return

    if system == "Darwin":
        script = f'display notification "{message}" with title "{title}"'
        _run_notification_command(["osascript", "-e", script])
        return

    if system == "Windows":
        if shutil.which("powershell"):
            command = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$n = New-Object System.Windows.Forms.NotifyIcon; "
                "$n.Icon = [System.Drawing.SystemIcons]::Information; "
                "$n.Visible = $true; "
                f'$n.BalloonTipTitle = "{title}"; '
                f'$n.BalloonTipText = "{message}"; '
                "$n.ShowBalloonTip(3000); Start-Sleep -Seconds 4; $n.Dispose();"
            )
            _run_notification_command(["powershell", "-NoProfile", "-Command", command])


def run_tests(extra_args: list[str] | None = None) -> int:
    """Run pytest and return the subprocess exit code."""

    command = build_pytest_command(extra_args)
    print(f"{PREFIX} Running: {' '.join(command)}")

    process: ProcessLike = subprocess.Popen(command, cwd=BASE_DIR, env=_build_subprocess_env())
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
    """Parse launcher arguments for a one-shot pytest run."""

    parser = argparse.ArgumentParser(description="Run pytest once.")
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Compatibility flag from legacy watcher mode (ignored).",
    )
    parser.add_argument(
        "--debounce",
        type=float,
        default=1.0,
        help="Compatibility flag from legacy watcher mode (ignored).",
    )
    parser.add_argument(
        "--latest",
        dest="latest",
        action="store_true",
        default=True,
        help="Compatibility flag from legacy watcher mode (ignored).",
    )
    parser.add_argument(
        "--no-latest",
        dest="latest",
        action="store_false",
        help="Compatibility flag from legacy watcher mode (ignored).",
    )
    parser.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="Additional args passed to pytest.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for VS Code test launcher tasks."""

    args = parse_args(argv)
    extra_args = args.extra_args
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]
    return run_tests(extra_args)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())

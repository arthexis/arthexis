#!/usr/bin/env python3
"""Cross-platform dispatcher for Arthexis root lifecycle entrypoints."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

COMMANDS: dict[str, str] = {
    "command": "Run an operational command through the existing command entrypoint.",
    "configure": "Configure an existing Arthexis installation.",
    "env-refresh": "Refresh the local Arthexis environment.",
    "error-report": "Generate or analyze an Arthexis diagnostic report.",
    "install": "Bootstrap or repair an Arthexis installation.",
    "start": "Start Arthexis.",
    "status": "Report installation and service status.",
    "stop": "Stop Arthexis services or development processes.",
    "uninstall": "Remove the Arthexis installation state.",
    "upgrade": "Upgrade the Arthexis checkout and environment.",
}


def _script_suffix() -> str:
    return ".bat" if os.name == "nt" else ".sh"


def _script_path(command: str) -> Path:
    return ROOT / f"{command}{_script_suffix()}"


def _invocation_name() -> str:
    return "arthexis.bat" if os.name == "nt" else "./arthexis.sh"


def _print_help() -> int:
    print(f"Usage: {_invocation_name()} <command> [args...]")
    print()
    print("Commands:")
    for command, description in COMMANDS.items():
        available = "" if _script_path(command).is_file() else " (not available on this OS)"
        print(f"  {command:<13} {description}{available}")
    print()
    print(f"Run {_invocation_name()} <command> --help for command-specific options.")
    return 0


def _run(command: str, args: list[str]) -> int:
    script = _script_path(command)
    if not script.is_file():
        platform = "Windows" if os.name == "nt" else "this platform"
        print(
            f"arthexis: '{command}' is not available on {platform}; "
            f"{script.name} was not found.",
            file=sys.stderr,
        )
        return 2

    if os.name == "nt":
        completed = subprocess.run([str(script), *args], cwd=ROOT, shell=True, check=False)
    else:
        completed = subprocess.run(["bash", str(script), *args], cwd=ROOT, check=False)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"help", "-h", "--help"}:
        return _print_help()

    command, *forwarded = args
    if command not in COMMANDS:
        print(f"arthexis: unknown command '{command}'.", file=sys.stderr)
        print(f"Run {_invocation_name()} help to list available commands.", file=sys.stderr)
        return 2

    return _run(command, forwarded)


if __name__ == "__main__":
    raise SystemExit(main())

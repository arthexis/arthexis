"""Shared operational command interface for shell and batch wrappers.

This module exposes an explicit allowlist of supported operational commands that
can be run through ``command.sh`` / ``command.bat``. Advanced administration and
all non-allowlisted Django commands should be run through ``manage.py``.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Sequence
from pathlib import Path

from utils.python_env import resolve_project_python

ALLOWED_COMMAND_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SUPPORTED_OPERATIONAL_COMMANDS: tuple[str, ...] = (
    "admin",
    "analytics",
    "availability",
    "benchmark",
    "browse",
    "changelog",
    "channels",
    "charger",
    "chargers",
    "coverage",
    "create",
    "diagnose",
    "doctor",
    "email",
    "env",
    "estimate",
    "feature",
    "features",
    "fixtures",
    "github",
    "godaddy",
    "good",
    "groups",
    "health",
    "https",
    "imager",
    "invite",
    "lcd",
    "message",
    "migrations",
    "nginx",
    "node",
    "notify",
    "ocpp",
    "password",
    "redis",
    "release",
    "repo",
    "rfid",
    "run_release_data_transforms",
    "runserver",
    "sensors",
    "startup",
    "summary",
    "test",
    "upgrade",
    "uptime",
    "utils",
)
COMMAND_ALIASES: dict[str, str] = {
    "diagnose": "diagnostics",
}
NAMESPACED_ARGUMENT_ALIASES: dict[tuple[str, str], tuple[str, ...]] = {
    ("https", "enable"): ("--enable", "--local"),
    ("nginx", "configure"): ("--configure",),
}


class CommandApiError(RuntimeError):
    """Raised for canonical command API failures that should be user-visible."""


def normalize_command_name(raw_command: str) -> str:
    """Normalize and validate command names to Django's underscore style."""
    if not ALLOWED_COMMAND_RE.match(raw_command):
        raise ValueError(
            "Invalid command name. Command names may only contain letters, numbers, underscores, and hyphens."
        )
    return raw_command.replace("-", "_")


def _format_command_list(commands: Sequence[str], *, width: int | None = None) -> str:
    """Format commands compactly for terminal output."""
    effective_width = width or min(
        max(shutil.get_terminal_size((100, 20)).columns, 72),
        120,
    )
    return textwrap.fill(
        ", ".join(commands),
        width=effective_width,
        initial_indent="  ",
        subsequent_indent="  ",
    )


def list_commands() -> int:
    """Print supported operational commands and usage hints."""
    print("Supported operational commands via command.sh / command.bat:")
    print(_format_command_list(SUPPORTED_OPERATIONAL_COMMANDS))
    print()
    print("Usage: ./command.sh <command> [args...]")
    print("Usage: ./command.sh list")
    print("For all other Django commands, use ./manage.py directly.")
    return 0


def _translate_namespaced_arguments(
    command: str, command_args: Sequence[str]
) -> tuple[str, ...]:
    """Translate supported namespaced action aliases while preserving trailing args."""
    if not command_args:
        return tuple(command_args)

    alias_args = NAMESPACED_ARGUMENT_ALIASES.get((command, command_args[0]))
    if alias_args is None:
        return tuple(command_args)

    return (*alias_args, *command_args[1:])


def _resolve_runtime_sigils(command_args: Sequence[str]) -> tuple[str, ...]:
    """Resolve lightweight runtime sigils supplied by shell entrypoints."""
    caller_cwd = os.environ.get("ARTHEXIS_CALLER_CWD", "")
    if not caller_cwd or not os.path.isabs(caller_cwd):
        return tuple(command_args)
    return tuple(
        caller_cwd if argument == "[CWD]" else argument for argument in command_args
    )


def run_command(base_dir: Path, raw_command: str, command_args: Sequence[str]) -> int:
    """Validate and execute a supported operational Django command."""
    try:
        command = normalize_command_name(raw_command)
    except ValueError as exc:
        raise CommandApiError(str(exc)) from exc

    if command not in SUPPORTED_OPERATIONAL_COMMANDS:
        raise CommandApiError(
            f"Unsupported operational command '{raw_command}'. "
            "Use './command.sh list' to see supported commands, "
            "or run the command through './manage.py' directly."
        )

    django_command = COMMAND_ALIASES.get(command, command)
    translated_args = _translate_namespaced_arguments(command, command_args)
    translated_args = _resolve_runtime_sigils(translated_args)
    process = subprocess.run(
        [
            resolve_project_python(base_dir),
            "manage.py",
            django_command,
            *translated_args,
        ],
        cwd=base_dir,
        check=False,
    )
    return process.returncode


def _build_help_parser() -> argparse.ArgumentParser:
    """Create a minimal parser used only for global help output."""
    parser = argparse.ArgumentParser(prog="arthexis cmd", add_help=True)
    parser.description = (
        "Run allowlisted operational commands through the shell/batch wrappers."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint used by shell and batch wrappers."""
    effective_argv = list(argv if argv is not None else sys.argv[1:])
    parser = _build_help_parser()

    if not effective_argv or effective_argv[0] in {"help", "list"}:
        return list_commands()
    if effective_argv[0] in {"-h", "--help"}:
        parser.print_help()
        print()
        return list_commands()

    base_dir = Path(__file__).resolve().parents[1]
    try:
        return run_command(base_dir, effective_argv[0], effective_argv[1:])
    except CommandApiError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

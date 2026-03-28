"""Shared operational command interface for shell and batch wrappers.

This module exposes an explicit allowlist of supported operational commands that
can be run through ``command.sh`` / ``command.bat``. Advanced administration and
all non-allowlisted Django commands should be run through ``manage.py``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from utils.python_env import resolve_project_python

ALLOWED_COMMAND_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SUPPORTED_OPERATIONAL_COMMANDS: tuple[str, ...] = (
    "admin",
    "analytics",
    "apply_nginx_config",
    "apply_release_migrations",
    "availability",
    "benchmark",
    "browse",
    "camera_service",
    "changelog",
    "channels",
    "charger",
    "chargers",
    "configure_site",
    "coverage",
    "create",
    "create_docs_admin",
    "dns_proxy",
    "email",
    "enable_local_https",
    "env",
    "estimate",
    "evergo",
    "feature",
    "features",
    "fixtures",
    "generate_certs",
    "generate_public_ocpp_sample_data",
    "godaddy",
    "good",
    "groups",
    "health",
    "https",
    "invite",
    "lcd",
    "leads",
    "message",
    "migrations",
    "nginx",
    "nginx_configure",
    "nginx_restart",
    "node",
    "notify",
    "ocpp",
    "odoo",
    "password",
    "preview",
    "prototype",
    "purge_net_messages",
    "purge_nodes",
    "rebrand",
    "reconcile_node_features_services",
    "record",
    "redis",
    "refresh_node_features",
    "register_site_apps",
    "release",
    "repo",
    "reset_ocpp_migrations",
    "rfid",
    "run_release_data_transforms",
    "run_scheduled_sql_reports",
    "runftpserver",
    "runserver",
    "shortcut_listener",
    "show_rfid_history",
    "simulator",
    "smb",
    "startup",
    "summary",
    "sync_desktop_shortcuts",
    "sync_registered_widgets",
    "sync_specials",
    "test",
    "test_login",
    "track_cp_forward",
    "upgrade",
    "uptime",
    "utils",
    "verify_certs",
    "video",
    "view_errors",
)


class CommandApiError(RuntimeError):
    """Raised for canonical command API failures that should be user-visible."""


def normalize_command_name(raw_command: str) -> str:
    """Normalize and validate command names to Django's underscore style."""
    if not ALLOWED_COMMAND_RE.match(raw_command):
        raise ValueError(
            "Invalid command name. Command names may only contain letters, numbers, underscores, and hyphens."
        )
    return raw_command.replace("-", "_")


def list_commands() -> int:
    """Print supported operational commands and usage hints."""
    print("Supported operational commands via command.sh / command.bat:")
    for command in SUPPORTED_OPERATIONAL_COMMANDS:
        print(command)
    print()
    print("Usage: ./command.sh <command> [args...]")
    print("Usage: ./command.sh list")
    print("For all other Django commands, use ./manage.py directly.")
    return 0


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

    process = subprocess.run(
        [resolve_project_python(base_dir), "manage.py", command, *command_args],
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

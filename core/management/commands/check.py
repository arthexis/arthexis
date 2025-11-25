from __future__ import annotations

import argparse
from collections import OrderedDict

from django.core.management import BaseCommand, CommandError, call_command
from django.core.management import get_commands, load_command_class


CHECK_COMMANDS = OrderedDict(
    [
        ("admin", "check_admin"),
        ("lcd", "check_lcd"),
        ("lcd-diagnostics", "lcd_check"),
        ("next-upgrade", "check_next_upgrade"),
        ("pypi", "check_pypi"),
        ("registration-ready", "check_registration_ready"),
        ("rfid", "rfid_check"),
        ("rfid-scan", "rfid_check"),
        ("time", "check_time"),
    ]
)


class Command(BaseCommand):
    """Provide a single entry point for running maintenance checks."""

    help = "Run a specific maintenance check or list the available checks"

    def add_arguments(self, parser):
        parser.add_argument(
            "target",
            nargs="?",
            help="Which check to run. Omit to list available checks.",
        )
        parser.add_argument(
            "command_args",
            nargs=argparse.REMAINDER,
            help="Arguments to forward to the selected check command.",
        )

    def handle(self, *args, **options):
        checks = self._resolve_checks()
        target = options.get("target")
        forwarded_args = options.get("command_args") or []

        if target is None:
            self._print_available_checks(checks)
            return

        normalized_target = target.replace("-", "_")
        if normalized_target not in checks:
            available = ", ".join(sorted(checks))
            raise CommandError(
                f"Unknown check '{target}'. Available checks: {available}"
            )

        command_name = checks[normalized_target]["command_name"]
        call_command(command_name, *forwarded_args)

    def _resolve_checks(self) -> OrderedDict[str, dict[str, str]]:
        """Pair target aliases with their command names and help text."""

        commands = get_commands()
        resolved = OrderedDict()
        for alias, command_name in CHECK_COMMANDS.items():
            normalized_alias = alias.replace("-", "_")
            resolved[normalized_alias] = {
                "command_name": command_name,
                "help": self._resolve_help(commands, command_name),
            }
        return resolved

    def _resolve_help(self, commands: dict[str, str], command_name: str) -> str:
        app_name = commands.get(command_name)
        if not app_name:
            return "No description available"

        command_class = load_command_class(app_name, command_name)
        help_text = getattr(command_class, "help", "").strip()
        return help_text or "No description available"

    def _print_available_checks(self, checks: OrderedDict[str, dict[str, str]]):
        self.stdout.write("Available checks:")
        for alias, metadata in checks.items():
            description = metadata["help"]
            display_alias = alias.replace("_", "-")
            self.stdout.write(f" - {display_alias}: {description}")

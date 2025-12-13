from __future__ import annotations

import argparse
from collections import OrderedDict

from django.core.management import CommandError, call_command
from django.core.management import get_commands, load_command_class
from django.core.management.commands.check import Command as SystemCheckCommand


CHECK_COMMANDS = OrderedDict(
    [
        ("admin", "check_admin"),
        ("lcd", "check_lcd"),
        ("lcd-diagnostics", "check_lcd_status"),
        ("next-upgrade", "check_next_upgrade"),
        ("pypi", "check_pypi"),
        ("nodes", "check_nodes"),
        ("registration-ready", "check_registration_ready"),
        ("rfid", "check_rfid"),
        ("rfid-scan", "rfid_check"),
        ("time", "check_time"),
    ]
)


class Command(SystemCheckCommand):
    """Provide a single entry point for running maintenance checks."""

    help = "Run a specific maintenance check or list the available checks"

    def add_arguments(self, parser):
        # Include Django's built-in system check arguments so this command can
        # be used transparently by the test runner and other tooling that
        # expect the standard interface.
        super().add_arguments(parser)

        parser.add_argument(
            "target",
            nargs="?",
            help="Which check to run. Omit to run system checks.",
        )
        parser.add_argument(
            "command_args",
            nargs=argparse.REMAINDER,
            help="Arguments to forward to the selected check command.",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            dest="list_checks",
            help="List available maintenance checks.",
        )

    def handle(self, *args, **options):
        checks = self._resolve_checks()
        target = options.pop("target", None)
        forwarded_args = options.pop("command_args", None) or []
        app_labels = options.get("app_labels") or []
        positional_args = list(args)
        list_checks = options.pop("list_checks", False)

        if target is None and app_labels:
            candidate = app_labels[0]
            normalized_candidate = candidate.replace("-", "_")
            if normalized_candidate in checks:
                target = candidate
                forwarded_args = app_labels[1:] + forwarded_args
                options["app_labels"] = []

        if target is None and positional_args:
            candidate = positional_args[0]
            normalized_candidate = candidate.replace("-", "_")
            if normalized_candidate in checks:
                target = candidate
                forwarded_args = positional_args[1:] + forwarded_args
                positional_args = []

        if target is None:
            if list_checks or (not positional_args and not options.get("app_labels")):
                self._print_available_checks(checks)
                return
            # Fall back to Django's built-in system checks so this command
            # remains compatible with the default test runner API.
            return super().handle(*positional_args, **options)

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

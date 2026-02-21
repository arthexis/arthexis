from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.ocpp.management.commands._ocpp_command_helpers import (
    add_trace_extract_arguments,
    warn_deprecated_command,
)


class Command(BaseCommand):
    help = "Extract recent OCPP transactions and session logs"

    def add_arguments(self, parser) -> None:
        add_trace_extract_arguments(parser)

    def handle(self, *args, **options) -> None:
        warn_deprecated_command("ocpp_extract", "ocpp trace extract")
        command_options = {
            key: value
            for key, value in options.items()
            if key in {"all", "next", "txn", "out", "log"} and value is not None
        }
        args = ["ocpp", "trace", "extract"]
        if "all" in command_options and command_options["all"]:
            args.append("--all")
        if "next" in command_options:
            args.extend(["--next", str(command_options["next"])])
        if "txn" in command_options:
            args.extend(["--txn", command_options["txn"]])
        if "out" in command_options:
            args.extend(["--out", command_options["out"]])
        if "log" in command_options:
            args.extend(["--log", command_options["log"]])
        call_command(*args, stdout=self.stdout, stderr=self.stderr)

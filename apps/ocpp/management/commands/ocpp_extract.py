from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.ocpp.management.commands._ocpp_command_helpers import (
    add_trace_extract_arguments,
    warn_deprecated_command,
)
from apps.ocpp.management.commands._trace_extract_impl import TraceExtractCommand


def run_trace_extract(**options) -> None:
    """Execute the trace extract flow with parsed options."""
    command = TraceExtractCommand()
    command.handle(**options)


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
        call_command("ocpp", "trace", "extract", **command_options)

"""Queue the startup LCD Net Message and emit a shell-friendly status."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.nodes.tasks import send_startup_net_message


class Command(BaseCommand):
    help = "Queue the startup Net Message and print queued/skipped status."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--port",
            default=None,
            help="Port value rendered in the startup LCD subject.",
        )
        parser.add_argument(
            "--lock-file",
            default=None,
            help="Optional LCD lock file path (defaults to .locks/lcd-high).",
        )

    def handle(self, *args, **options):
        status = send_startup_net_message(
            lock_file=options.get("lock_file"),
            port=options.get("port"),
        )
        self.stdout.write(status)

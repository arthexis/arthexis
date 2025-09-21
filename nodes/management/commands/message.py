"""Management command to broadcast :class:`~nodes.models.NetMessage` entries."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from nodes.models import NetMessage


class Command(BaseCommand):
    """Send a network message across nodes."""

    help = "Broadcast a Net Message to the network"

    def add_arguments(self, parser) -> None:
        parser.add_argument("subject", help="Subject or first line of the message")
        parser.add_argument(
            "body",
            nargs="?",
            default="",
            help="Optional body text for the message",
        )
        parser.add_argument(
            "--seen",
            nargs="+",
            dest="seen",
            help="UUIDs of nodes that have already seen the message",
        )

    def handle(self, *args, **options):
        subject: str = options["subject"]
        body: str = options["body"]
        seen: list[str] | None = options.get("seen")

        NetMessage.broadcast(subject=subject, body=body, seen=seen)
        self.stdout.write(self.style.SUCCESS("Net message broadcast"))

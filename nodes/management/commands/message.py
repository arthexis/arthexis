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
            "--reach",
            dest="reach",
            help="Optional node role name that limits propagation",
        )
        parser.add_argument(
            "--directionality",
            dest="directionality",
            choices=[choice.value for choice in NetMessage.Directionality],
            help="Restrict propagation to upstream, downstream, peers, or broadcast",
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
        reach: str | None = options.get("reach")
        directionality: str | None = options.get("directionality")
        seen: list[str] | None = options.get("seen")

        NetMessage.broadcast(
            subject=subject,
            body=body,
            reach=reach,
            directionality=directionality,
            seen=seen,
        )
        self.stdout.write(self.style.SUCCESS("Net message broadcast"))

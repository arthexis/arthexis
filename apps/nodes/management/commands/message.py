"""Management command to broadcast :class:`~nodes.models.NetMessage` entries."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.nodes.models import NetMessage


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
            "--seen",
            nargs="+",
            dest="seen",
            help="UUIDs of nodes that have already seen the message",
        )
        parser.add_argument(
            "--lcd-channel-type",
            dest="lcd_channel_type",
            help="LCD channel type to target (for example low, high, clock, uptime)",
        )
        parser.add_argument(
            "--lcd-channel-num",
            dest="lcd_channel_num",
            type=int,
            help="LCD channel number to target",
        )

    def handle(self, *args, **options):
        subject: str = options["subject"]
        body: str = options["body"]
        reach: str | None = options.get("reach")
        seen: list[str] | None = options.get("seen")
        lcd_channel_type: str | None = options.get("lcd_channel_type")
        lcd_channel_num: int | None = options.get("lcd_channel_num")

        NetMessage.broadcast(
            subject=subject,
            body=body,
            reach=reach,
            seen=seen,
            lcd_channel_type=lcd_channel_type,
            lcd_channel_num=lcd_channel_num,
        )
        self.stdout.write(self.style.SUCCESS("Net message broadcast"))

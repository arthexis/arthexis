"""Management command to broadcast :class:`~nodes.models.NetMessage` entries."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

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
            "--expires-at",
            dest="expires_at",
            help="Optional ISO 8601 timestamp when the message should expire",
        )

    def handle(self, *args, **options):
        subject: str = options["subject"]
        body: str = options["body"]
        reach: str | None = options.get("reach")
        seen: list[str] | None = options.get("seen")
        expires_at_raw = options.get("expires_at")
        expires_at = None
        if expires_at_raw:
            expires_at = parse_datetime(expires_at_raw)
            if expires_at is None:
                raise CommandError("--expires-at must be a valid ISO 8601 datetime")
            if expires_at.tzinfo is None:
                from django.utils import timezone

                expires_at = timezone.make_aware(
                    expires_at, timezone.get_current_timezone()
                )

        NetMessage.broadcast(
            subject=subject,
            body=body,
            reach=reach,
            seen=seen,
            expires_at=expires_at,
        )
        self.stdout.write(self.style.SUCCESS("Net message broadcast"))

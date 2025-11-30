"""Management command to purge all :class:`~nodes.models.NetMessage` entries."""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.nodes.models import NetMessage, PendingNetMessage


class Command(BaseCommand):
    """Remove all network messages from the system."""

    help = "Delete every Net Message and pending delivery from the system"

    def handle(self, *args, **options):
        deleted, model_counts = NetMessage.objects.all().delete()
        message_count = model_counts.get(NetMessage._meta.label, 0)
        pending_count = model_counts.get(PendingNetMessage._meta.label, 0)

        if deleted:
            suffix = "s" if message_count != 1 else ""
            message = f"Deleted {message_count} net message{suffix}"
            if pending_count:
                pending_label = (
                    "pending queue entry"
                    if pending_count == 1
                    else "pending queue entries"
                )
                message = f"{message}, cleared {pending_count} {pending_label}"
            related = deleted - message_count - pending_count
            if related > 0:
                related_label = "related row" if related == 1 else "related rows"
                message = f"{message}, removed {related} {related_label}"
            self.stdout.write(
                self.style.SUCCESS(
                    f"{message}."
                )
            )
        else:
            self.stdout.write("No net messages found.")

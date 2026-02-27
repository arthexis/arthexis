"""Signal handlers for Fitbit integration."""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.fitbit.services import dispatch_net_messages_to_connections
from apps.nodes.models import NetMessage


@receiver(post_save, sender=NetMessage)
def enqueue_fitbit_delivery(sender, instance: NetMessage, created: bool, **kwargs) -> None:
    """Create Fitbit delivery records for newly created fitbit-targeted Net Messages."""
    if not created:
        return
    if (instance.lcd_channel_type or "").strip().lower() != "fitbit":
        return
    dispatch_net_messages_to_connections(limit=10)

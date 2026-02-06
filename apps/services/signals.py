from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .lifecycle import write_lifecycle_config
from .models import LifecycleService


@receiver(post_save, sender=LifecycleService)
@receiver(post_delete, sender=LifecycleService)
def refresh_lifecycle_service_config(sender, **kwargs) -> None:
    transaction.on_commit(lambda: write_lifecycle_config())

"""Signals for automatic classification record creation during ingestion."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.content.storage.models import MediaFile

from .services import enqueue_media_for_classification


@receiver(post_save, sender=MediaFile)
def create_pending_classification_for_media(sender, instance: MediaFile, created: bool, **kwargs):
    """Queue ingested media for classifier processing when records are created."""

    if not created:
        return
    enqueue_media_for_classification(instance)

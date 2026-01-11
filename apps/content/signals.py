from __future__ import annotations

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.content.classifiers import (
    run_default_classifiers,
    should_skip_default_classifiers,
)
from apps.content.models import ContentSample


@receiver(post_save, sender=ContentSample)
def run_classifiers_on_sample_creation(sender, instance: ContentSample, created: bool, **_: object):
    """Execute default classifiers whenever a new sample is stored."""

    if not created or should_skip_default_classifiers():
        return
    run_default_classifiers(instance)


@receiver(pre_save, sender=ContentSample)
def ensure_content_sample_node(sender, instance: ContentSample, **_: object) -> None:
    """Attach the local node when none is specified."""

    if instance.node_id is not None:
        return
    from apps.nodes.models import Node

    instance.node = Node.get_local()

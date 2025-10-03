"""Signal handlers for the :mod:`nodes` application."""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from .classifiers import run_default_classifiers, should_skip_default_classifiers
from .models import ContentSample


@receiver(post_save, sender=ContentSample)
def run_classifiers_on_sample_creation(sender, instance: ContentSample, created: bool, **_: object):
    """Execute default classifiers whenever a new sample is stored."""

    if not created or should_skip_default_classifiers():
        return
    run_default_classifiers(instance)

"""Signals for Shortcut Management lifecycle hooks."""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Shortcut
from .runtime import ensure_shortcut_listener_feature_enabled


@receiver(post_save, sender=Shortcut)
def _ensure_listener_assignment(sender, instance: Shortcut, **kwargs) -> None:
    """Auto-enable shortcut listener node feature after server shortcut updates."""

    del sender, kwargs
    if instance.kind != Shortcut.Kind.SERVER or not instance.is_active:
        return
    ensure_shortcut_listener_feature_enabled()

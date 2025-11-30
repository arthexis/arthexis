"""Signal handlers for the :mod:`nodes` application."""

from __future__ import annotations

from django.conf import settings
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def _revoke_public_wifi_when_inactive(sender, instance, **kwargs):
    if instance.is_active:
        return
    from apps.core import public_wifi

    public_wifi.revoke_public_access_for_user(instance)


@receiver(post_delete, sender=settings.AUTH_USER_MODEL)
def _cleanup_public_wifi_on_delete(sender, instance, **kwargs):
    from apps.core import public_wifi

    public_wifi.revoke_public_access_for_user(instance)

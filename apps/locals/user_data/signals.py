from __future__ import annotations

from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save
from django.dispatch import receiver

from .fixtures import load_shared_user_fixtures, load_user_fixtures


@receiver(user_logged_in)
def _on_login(sender, request, user, **kwargs):
    load_user_fixtures(user, include_shared=True)

    if not (
        getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)
    ):
        return

    # Login Net Messages were previously sent for staff authentication events.
    # They have been retired in favor of less noisy auditing, so no additional
    # side effects occur here beyond loading fixtures.


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def _on_user_created(sender, instance, created, **kwargs):
    if created:
        load_shared_user_fixtures(force=True, user=instance)
        load_user_fixtures(instance)

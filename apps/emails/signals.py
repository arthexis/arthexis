"""Signal handlers for the :mod:`emails` application."""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.counters.models import DashboardRule
from apps.emails.models import EmailInbox, EmailOutbox


@receiver(post_save, sender=EmailInbox)
@receiver(post_delete, sender=EmailInbox)
@receiver(post_save, sender=EmailOutbox)
@receiver(post_delete, sender=EmailOutbox)
def invalidate_email_profile_rule_cache(**_kwargs) -> None:
    """Invalidate cached dashboard rule status for email profile checks."""

    DashboardRule.invalidate_model_cache(EmailInbox)
    DashboardRule.invalidate_model_cache(EmailOutbox)

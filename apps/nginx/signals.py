"""Signal handlers for the :mod:`nginx` application."""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.counters.models import DashboardRule
from apps.nginx.models import SiteConfiguration


@receiver(post_save, sender=SiteConfiguration)
@receiver(post_delete, sender=SiteConfiguration)
def invalidate_nginx_dashboard_rule_cache(**_kwargs) -> None:
    """Invalidate cached dashboard rule status for nginx site validations."""

    DashboardRule.invalidate_model_cache(SiteConfiguration)

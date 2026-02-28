from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.counters.models import DashboardRule

from .models import Simulator


@receiver([post_save, post_delete], sender=Simulator)
def invalidate_simulator_dashboard_rule_cache(sender, **_kwargs) -> None:
    """Invalidate dashboard rule cache for CP simulator default checks."""

    DashboardRule.invalidate_model_cache(sender)

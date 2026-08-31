"""Signal handlers for the :mod:`nodes` application."""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.counters.models import DashboardRule
from apps.nodes.models import Node


@receiver(post_save, sender=Node)
@receiver(post_delete, sender=Node)
def invalidate_node_dashboard_rule_cache(**_kwargs) -> None:
    """Invalidate cached dashboard rule status for node health checks."""

    DashboardRule.invalidate_model_cache(Node)

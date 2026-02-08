import logging
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.counters.models import DashboardRule
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment

from .models import Charger, Simulator

logger = logging.getLogger(__name__)

CHARGE_POINT_FEATURE_SLUG = "charge-points"


@receiver(post_save, sender=Charger)
def enable_charge_point_feature(sender, instance, created, **kwargs):
    if not created:
        return
    node = Node.get_local()
    if not node:
        return
    feature = NodeFeature.objects.filter(slug=CHARGE_POINT_FEATURE_SLUG).first()
    if not feature:
        logger.debug("Charge point feature %s missing; skipping", CHARGE_POINT_FEATURE_SLUG)
        return
    NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)


@receiver([post_save, post_delete], sender=Simulator)
def invalidate_simulator_dashboard_rule_cache(sender, **_kwargs) -> None:
    """Invalidate dashboard rule cache for CP simulator default checks."""

    DashboardRule.invalidate_model_cache(sender)

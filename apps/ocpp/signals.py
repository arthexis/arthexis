import logging
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment

from .models import Charger

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

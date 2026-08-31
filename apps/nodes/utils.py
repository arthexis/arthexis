import logging
from pathlib import Path

from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError

from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment


class FeatureChecker:
    def __init__(self) -> None:
        self._cache: dict[str, bool] = {}

    def is_enabled(self, slug: str) -> bool:
        if slug in self._cache:
            return self._cache[slug]
        try:
            feature = NodeFeature.objects.filter(slug=slug).first()
        except (OperationalError, ProgrammingError):
            feature = None
        try:
            enabled = bool(feature and feature.is_enabled)
        except (OperationalError, ProgrammingError):
            enabled = False
        self._cache[slug] = enabled
        return enabled


def ensure_feature_enabled(
    slug: str,
    *,
    node: Node | None = None,
    logger: logging.Logger | None = None,
) -> bool:
    """Attempt to enable a node feature if it is available."""

    target = node or Node.get_local()
    if not target:
        return False

    feature = NodeFeature.objects.filter(slug=slug).first()
    if not feature:
        return False

    if target.has_feature(slug):
        return True

    lazy_slugs = getattr(target, "LAZY_AUTO_DETECTION_FEATURE_SLUGS", set())
    if target.is_local and slug in lazy_slugs:
        try:
            base_dir = Path(settings.BASE_DIR)
            base_path = target.get_base_path()
            if target._detect_auto_feature(slug, base_dir=base_dir, base_path=base_path):
                NodeFeatureAssignment.objects.update_or_create(
                    node=target, feature=feature
                )
                return True
        except Exception:
            if logger:
                logger.exception("Unable to lazily detect feature %s", slug)
        return False

    try:
        target.refresh_features()
    except Exception:
        if logger:
            logger.exception("Unable to refresh features for %s", slug)
    return target.has_feature(slug)


__all__ = [
    "FeatureChecker",
    "ensure_feature_enabled",
]

"""Helpers for node device-detection synchronization."""

from __future__ import annotations

from apps.nodes.device_sync import sync_detected_devices
from apps.nodes.feature_detection import is_feature_active_for_node


def sync_feature_detected_devices(
    *,
    model_cls,
    node,
    feature_slug: str,
    detected,
    defaults_getter,
    identifier_getter,
    return_objects: bool = False,
):
    """Synchronize detected devices when the required node feature is enabled."""

    if not is_feature_active_for_node(node=node, slug=feature_slug):
        if return_objects:
            return 0, 0, [], []
        return 0, 0

    return sync_detected_devices(
        model_cls=model_cls,
        node=node,
        detected=detected,
        identifier_getter=identifier_getter,
        defaults_getter=defaults_getter,
        return_objects=return_objects,
    )

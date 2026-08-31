from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from apps.nodes.feature_detection import NodeFeatureDetectionRegistry
from apps.sensors.usb_inventory import (
    USB_INVENTORY_NODE_FEATURE_SLUG,
    usb_inventory_available,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.nodes.models import Node


def check_node_feature(
    slug: str,
    *,
    node: Node,
    base_dir: Path,
    base_path: Path,
) -> bool | None:
    """Return whether sensors can auto-enable a node feature."""

    del base_dir, base_path
    if slug != USB_INVENTORY_NODE_FEATURE_SLUG:
        return None
    return usb_inventory_available(node=node)


def setup_node_feature(
    slug: str,
    *,
    node: Node,
    base_dir: Path,
    base_path: Path,
) -> bool | None:
    """Allow the sensors app to own USB inventory auto-detection."""

    return check_node_feature(
        slug,
        node=node,
        base_dir=base_dir,
        base_path=base_path,
    )


def register_node_feature_detection(registry: NodeFeatureDetectionRegistry) -> None:
    """Register sensors feature auto-detection callbacks."""

    registry.register(
        USB_INVENTORY_NODE_FEATURE_SLUG,
        check=check_node_feature,
        setup=setup_node_feature,
    )


__all__ = [
    "check_node_feature",
    "register_node_feature_detection",
    "setup_node_feature",
]

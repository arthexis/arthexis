"""Explicitly approved node feature detector registrars."""

from __future__ import annotations

from collections.abc import Sequence

from apps.cards.node_features import (
    register_node_feature_detection as register_cards_features,
)
from apps.nodes.node_features import (
    register_node_feature_detection as register_nodes_features,
)
from apps.playwright.node_features import (
    register_node_feature_detection as register_playwright_features,
)
from apps.screens.node_features import (
    register_node_feature_detection as register_screens_features,
)
from apps.sensors.node_features import (
    register_node_feature_detection as register_sensors_features,
)
from apps.summary.node_features import (
    register_node_feature_detection as register_summary_features,
)

from .feature_detection import DetectionRegistrar

APPROVED_NODE_FEATURE_REGISTRARS: Sequence[DetectionRegistrar] = (
    register_nodes_features,
    register_cards_features,
    register_playwright_features,
    register_screens_features,
    register_sensors_features,
    register_summary_features,
)

__all__ = ["APPROVED_NODE_FEATURE_REGISTRARS"]

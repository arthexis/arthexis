"""Explicitly approved node feature detector registrars."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from importlib import import_module

from django.apps import apps as django_apps

from apps.nodes.node_features import (
    register_node_feature_detection as register_nodes_features,
)

from .feature_detection import DetectionRegistrar

APPROVED_NODE_FEATURE_REGISTRARS: tuple[DetectionRegistrar, ...] = (
    register_nodes_features,
)

OPTIONAL_NODE_FEATURE_REGISTRARS: Sequence[tuple[str, str]] = (
    ("apps.cards", "apps.cards.node_features"),
    ("apps.docs", "apps.docs.node_features"),
    ("apps.imager", "apps.imager.node_features"),
    ("apps.sensors", "apps.sensors.node_features"),
    ("apps.summary", "apps.summary.node_features"),
)


def iter_approved_node_feature_registrars() -> Iterator[DetectionRegistrar]:
    """Yield approved node feature registrars for installed apps only."""

    yield from APPROVED_NODE_FEATURE_REGISTRARS
    for app_config_name, module_name in OPTIONAL_NODE_FEATURE_REGISTRARS:
        if not django_apps.is_installed(app_config_name):
            continue
        module = import_module(module_name)
        registrar = getattr(module, "register_node_feature_detection")
        yield registrar


__all__ = [
    "APPROVED_NODE_FEATURE_REGISTRARS",
    "OPTIONAL_NODE_FEATURE_REGISTRARS",
    "iter_approved_node_feature_registrars",
]

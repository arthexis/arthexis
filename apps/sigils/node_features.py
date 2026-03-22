from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from apps.nodes.feature_detection import NodeFeatureDetectionRegistry

from .scanner import is_llvm_scanner_runtime_available

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.nodes.models import Node


LLVM_SIGILS_SLUG = "llvm-sigils"


def check_node_feature(
    slug: str,
    *,
    node: "Node",
    base_dir: Path | None = None,
    base_path: Path | None = None,
) -> bool | None:
    """Return runtime availability for the LLVM sigil scanner feature slug."""

    del node, base_dir, base_path
    if slug != LLVM_SIGILS_SLUG:
        return None
    return is_llvm_scanner_runtime_available()


def setup_node_feature(
    slug: str,
    *,
    node: "Node",
    base_dir: Path | None = None,
    base_path: Path | None = None,
) -> bool | None:
    """Allow sigils app to own setup/detection for llvm-sigils."""

    return check_node_feature(
        slug,
        node=node,
        base_dir=base_dir,
        base_path=base_path,
    )


def register_node_feature_detection(registry: NodeFeatureDetectionRegistry) -> None:
    """Register llvm-sigils node feature detection callbacks."""

    registry.register(LLVM_SIGILS_SLUG, check=check_node_feature, setup=setup_node_feature)


__all__ = [
    "check_node_feature",
    "register_node_feature_detection",
    "setup_node_feature",
]

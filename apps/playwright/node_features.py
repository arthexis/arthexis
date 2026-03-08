from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from apps.nodes.feature_detection import NodeFeatureDetectionRegistry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.nodes.models import Node


ENGINE_TO_SLUG = {
    "chromium": "playwright-browser-chromium",
    "firefox": "playwright-browser-firefox",
    "webkit": "playwright-browser-webkit",
}


def _playwright_engine_available(engine: str) -> bool:
    """Return whether Playwright has an executable browser for ``engine``."""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False

    try:
        with sync_playwright() as playwright:
            launcher = getattr(playwright, engine, None)
            if launcher is None:
                return False
            executable = Path(launcher.executable_path)
    except Exception:
        return False

    return executable.exists() and os.access(executable, os.X_OK)


def check_node_feature(
    slug: str,
    *,
    node: "Node",
    base_dir: Path | None = None,
    base_path: Path | None = None,
) -> bool | None:
    """Return Playwright browser availability for matching feature slugs."""

    del node, base_dir, base_path
    for engine, engine_slug in ENGINE_TO_SLUG.items():
        if slug == engine_slug:
            return _playwright_engine_available(engine)
    return None


def setup_node_feature(
    slug: str,
    *,
    node: "Node",
    base_dir: Path | None = None,
    base_path: Path | None = None,
) -> bool | None:
    """Allow Playwright browser features to own setup/detection lifecycle."""

    return check_node_feature(
        slug,
        node=node,
        base_dir=base_dir,
        base_path=base_path,
    )


def register_node_feature_detection(registry: NodeFeatureDetectionRegistry) -> None:
    """Register Playwright browser feature detectors."""

    for slug in sorted(ENGINE_TO_SLUG.values()):
        registry.register(slug, check=check_node_feature, setup=setup_node_feature)


__all__ = [
    "check_node_feature",
    "register_node_feature_detection",
    "setup_node_feature",
]

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import importlib
import logging
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from django.apps import apps as django_apps
from django.conf import settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .models import Node


logger = logging.getLogger(__name__)

DetectionCallable = Callable[..., bool | None]


@dataclass(frozen=True)
class NodeFeatureDetector:
    """Detection callbacks for a single feature slug."""

    slug: str
    check: DetectionCallable | None = None
    setup: DetectionCallable | None = None

    def run(
        self,
        slug: str,
        *,
        node: Node,
        base_dir: Path,
        base_path: Path,
    ) -> bool | None:
        """Execute detector callbacks and normalize return values."""

        result: bool | None = None
        if callable(self.check):
            result = self.check(
                slug,
                node=node,
                base_dir=base_dir,
                base_path=base_path,
            )

        if result is None and callable(self.setup):
            return self.setup(
                slug,
                node=node,
                base_dir=base_dir,
                base_path=base_path,
            )

        if result and callable(self.setup):
            setup_result = self.setup(
                slug,
                node=node,
                base_dir=base_dir,
                base_path=base_path,
            )
            if setup_result is not None:
                return bool(setup_result)

        if result is None:
            return None
        return bool(result)


class NodeFeatureDetectionRegistry:
    """Discover and orchestrate app-owned auto-detection callbacks."""

    def __init__(self) -> None:
        self._detectors: dict[str, list[NodeFeatureDetector]] = {}
        self._discovered = False
        self._lock = RLock()

    def reset(self) -> None:
        """Reset discovered registrations for fresh discovery."""
        with self._lock:
            self._detectors.clear()
            self._discovered = False

    def register(
        self,
        slug: str,
        *,
        check: DetectionCallable | None = None,
        setup: DetectionCallable | None = None,
    ) -> None:
        """Register a detector pair for ``slug``."""
        detector = NodeFeatureDetector(slug=slug, check=check, setup=setup)
        with self._lock:
            self._detectors.setdefault(slug, []).append(detector)

    def discover(self) -> None:
        """Load detector registration from installed apps."""
        with self._lock:
            if self._discovered:
                return

            self._detectors.clear()

            for app_config in django_apps.get_app_configs():
                module_name = f"{app_config.name}.node_features"
                try:
                    module = importlib.import_module(module_name)
                except ModuleNotFoundError as exc:
                    if exc.name != module_name:
                        logger.exception(
                            "Node feature detector import failed for %s", module_name
                        )
                    continue
                except Exception:
                    logger.exception(
                        "Node feature detector import failed for %s", module_name
                    )
                    continue

                register = getattr(module, "register_node_feature_detection", None)
                if callable(register):
                    try:
                        register(self)
                    except Exception:
                        logger.exception(
                            "Node feature detector registration failed for %s",
                            module_name,
                        )
                    continue

                check = getattr(module, "check_node_feature", None)
                setup = getattr(module, "setup_node_feature", None)
                if callable(check) or callable(setup):
                    self.register("*", check=check, setup=setup)

            self._discovered = True

    def detect(
        self,
        slug: str,
        *,
        node: Node,
        base_dir: Path,
        base_path: Path,
    ) -> bool | None:
        """Run detectors for ``slug`` in registration order."""
        self.discover()
        with self._lock:
            detectors = [
                *self._detectors.get(slug, []),
                *self._detectors.get("*", []),
            ]

        for detector in detectors:
            try:
                result = detector.run(
                    slug,
                    node=node,
                    base_dir=base_dir,
                    base_path=base_path,
                )
            except Exception:
                logger.exception("Node feature detector failed for %s", slug)
                continue
            if result is not None:
                return bool(result)
        return None


def is_feature_active_for_node(
    *,
    node: "Node",
    slug: str,
    base_dir: Path | None = None,
    base_path: Path | None = None,
) -> bool:
    """Return whether ``slug`` is active for ``node`` via assignments or detection."""

    has_feature = getattr(node, "has_feature", None)
    if callable(has_feature) and has_feature(slug):
        return True

    is_local_attr = getattr(node, "is_local", None)
    if is_local_attr is not None and not bool(is_local_attr):
        return False

    resolved_base_dir = base_dir or Path(settings.BASE_DIR)
    get_base_path = getattr(node, "get_base_path", None)
    if not callable(get_base_path):
        return False
    resolved_base_path = base_path or get_base_path()
    return bool(
        node._detect_auto_feature(
            slug,
            base_dir=resolved_base_dir,
            base_path=resolved_base_path,
        )
    )


def is_local_node_feature_active(slug: str) -> bool:
    """Return whether local node supports ``slug`` through assignment or detection."""

    node_model = django_apps.get_model("nodes", "Node")
    if node_model is None:
        return False

    node = node_model.get_local()
    if node is None:
        return False

    return is_feature_active_for_node(node=node, slug=slug)


node_feature_detection_registry = NodeFeatureDetectionRegistry()

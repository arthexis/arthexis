from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from django.apps import apps as django_apps
from django.conf import settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .models import Node


logger = logging.getLogger(__name__)

DetectionCallable = Callable[..., bool | None]
DetectionRegistrar = Callable[["NodeFeatureDetectionRegistry"], None]


def _invoke_detector(
    callback: DetectionCallable,
    slug: str,
    *,
    node: Node,
    base_dir: Path,
    base_path: Path,
) -> bool | None:
    """Invoke detector callbacks using the canonical signature."""

    return callback(
        slug,
        node=node,
        base_dir=base_dir,
        base_path=base_path,
    )


def _validate_detector_callback(callback: DetectionCallable, *, slug: str) -> None:
    """Ensure detector callbacks use the canonical ``slug, *, node, base_dir, base_path`` signature."""

    signature = inspect.signature(callback)
    parameters = list(signature.parameters.values())
    expected_names = ["slug", "node", "base_dir", "base_path"]

    if len(parameters) != len(expected_names):
        raise TypeError(
            f"Detector callback for '{slug}' must accept exactly {expected_names}."
        )

    expected_kinds = [
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.KEYWORD_ONLY,
    ]
    for parameter, expected_name, expected_kind in zip(
        parameters,
        expected_names,
        expected_kinds,
        strict=True,
    ):
        if parameter.name != expected_name or parameter.kind is not expected_kind:
            raise TypeError(
                f"Detector callback for '{slug}' must use signature "
                "(slug, *, node, base_dir, base_path)."
            )
        if parameter.default is not inspect.Parameter.empty:
            raise TypeError(
                f"Detector callback for '{slug}' cannot define defaults in canonical signature."
            )


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
            result = _invoke_detector(
                self.check,
                slug,
                node=node,
                base_dir=base_dir,
                base_path=base_path,
            )

        if result is None and callable(self.setup):
            return _invoke_detector(
                self.setup,
                slug,
                node=node,
                base_dir=base_dir,
                base_path=base_path,
            )

        if result and callable(self.setup):
            setup_result = _invoke_detector(
                self.setup,
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

        if not isinstance(slug, str) or not slug.strip():
            raise ValueError("Detector slug must be a non-empty string.")
        if check is None and setup is None:
            raise ValueError(f"Detector for '{slug}' must provide at least one callback.")
        if check is not None:
            if not callable(check):
                raise TypeError(f"Detector 'check' callback for '{slug}' must be callable.")
            _validate_detector_callback(check, slug=slug)
        if setup is not None:
            if not callable(setup):
                raise TypeError(f"Detector 'setup' callback for '{slug}' must be callable.")
            _validate_detector_callback(setup, slug=slug)

        detector = NodeFeatureDetector(slug=slug, check=check, setup=setup)
        with self._lock:
            self._detectors.setdefault(slug, []).append(detector)

    def discover(self) -> None:
        """Load detector registrations from the explicit approved detector registry."""
        with self._lock:
            if self._discovered:
                return

            self._detectors.clear()

            from .feature_registry import APPROVED_NODE_FEATURE_REGISTRARS

            for registrar in APPROVED_NODE_FEATURE_REGISTRARS:
                if not callable(registrar):
                    raise TypeError("Node feature registrar entries must be callable.")
                registrar(self)

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
    node: Node,
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

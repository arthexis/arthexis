import logging
from pathlib import Path

from django.db.utils import OperationalError, ProgrammingError

from apps.content import utils as content_utils
from apps.nodes.models import Node, NodeFeature

SCREENSHOT_DIR = content_utils.SCREENSHOT_DIR
DEFAULT_SCREENSHOT_RESOLUTION = content_utils.DEFAULT_SCREENSHOT_RESOLUTION


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

    try:
        target.refresh_features()
    except Exception:
        if logger:
            logger.exception("Unable to refresh features for %s", slug)
    return target.has_feature(slug)


def capture_screenshot(
    url: str,
    cookies=None,
    *,
    width: int | None = None,
    height: int | None = None,
) -> Path:
    """Backward-compatible wrapper for :func:`apps.content.utils.capture_screenshot`."""

    return content_utils.capture_screenshot(
        url,
        cookies,
        width=width,
        height=height,
    )


def capture_local_screenshot() -> Path:
    """Backward-compatible wrapper for :func:`apps.content.utils.capture_local_screenshot`."""

    return content_utils.capture_local_screenshot()


def capture_and_save_screenshot(
    url: str | None = None,
    port: int | None = None,
    method: str = "TASK",
    local: bool = False,
    *,
    width: int | None = None,
    height: int | None = None,
    logger: logging.Logger | None = None,
    log_capture_errors: bool = False,
):
    """Backward-compatible wrapper for :func:`apps.content.utils.capture_and_save_screenshot`."""

    return content_utils.capture_and_save_screenshot(
        url=url,
        port=port,
        method=method,
        local=local,
        width=width,
        height=height,
        logger=logger,
        log_capture_errors=log_capture_errors,
    )


def save_screenshot(
    path: Path,
    node=None,
    method: str = "",
    transaction_uuid=None,
    *,
    content: str | None = None,
    user=None,
    link_duplicates: bool = False,
):
    """Backward-compatible wrapper for :func:`apps.content.utils.save_screenshot`."""

    return content_utils.save_screenshot(
        path,
        node=node,
        method=method,
        transaction_uuid=transaction_uuid,
        content=content,
        user=user,
        link_duplicates=link_duplicates,
    )


__all__ = [
    "capture_screenshot",
    "capture_local_screenshot",
    "capture_and_save_screenshot",
    "save_screenshot",
    "SCREENSHOT_DIR",
    "DEFAULT_SCREENSHOT_RESOLUTION",
    "FeatureChecker",
    "ensure_feature_enabled",
]

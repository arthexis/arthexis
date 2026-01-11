import logging
from pathlib import Path

from apps.content import utils as content_utils

SCREENSHOT_DIR = content_utils.SCREENSHOT_DIR
DEFAULT_SCREENSHOT_RESOLUTION = content_utils.DEFAULT_SCREENSHOT_RESOLUTION


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
    port: int = 8888,
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
]

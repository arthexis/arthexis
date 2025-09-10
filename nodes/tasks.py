import logging
from pathlib import Path

import pyperclip
from pyperclip import PyperclipException
from celery import shared_task

from .models import ContentSample, Node
from .utils import capture_screenshot, save_screenshot

logger = logging.getLogger(__name__)


@shared_task
def sample_clipboard() -> None:
    """Save current clipboard contents to a :class:`ContentSample` entry."""
    try:
        content = pyperclip.paste()
    except PyperclipException as exc:  # pragma: no cover - depends on OS clipboard
        logger.error("Clipboard error: %s", exc)
        return
    if not content:
        logger.info("Clipboard is empty")
        return
    if ContentSample.objects.filter(content=content, kind=ContentSample.TEXT).exists():
        logger.info("Duplicate clipboard content; sample not created")
        return
    node = Node.get_local()
    ContentSample.objects.create(content=content, node=node, kind=ContentSample.TEXT)


@shared_task
def capture_node_screenshot(
    url: str | None = None, port: int = 8000, method: str = "TASK"
) -> str:
    """Capture a screenshot of ``url`` and record it as a :class:`ContentSample`."""
    if url is None:
        url = f"http://localhost:{port}"
    try:
        path: Path = capture_screenshot(url)
    except Exception as exc:  # pragma: no cover - depends on selenium setup
        logger.error("Screenshot capture failed: %s", exc)
        return ""
    node = Node.get_local()
    save_screenshot(path, node=node, method=method)
    return str(path)

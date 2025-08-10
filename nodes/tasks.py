import logging
import socket
from pathlib import Path

import pyperclip
from pyperclip import PyperclipException
from celery import shared_task

from .models import Sample, Node, NodeScreenshot
from .utils import capture_screenshot

logger = logging.getLogger(__name__)


@shared_task
def sample_clipboard() -> None:
    """Save current clipboard contents to a :class:`Sample` entry."""
    try:
        content = pyperclip.paste()
    except PyperclipException as exc:  # pragma: no cover - depends on OS clipboard
        logger.error("Clipboard error: %s", exc)
        return
    if not content:
        logger.info("Clipboard is empty")
        return
    Sample.objects.create(content=content)


@shared_task
def capture_node_screenshot(url: str, port: int = 8000) -> str:
    """Capture a screenshot of ``url`` and record it as a :class:`NodeScreenshot`."""
    path: Path = capture_screenshot(url)
    hostname = socket.gethostname()
    node = Node.objects.filter(hostname=hostname, port=port).first()
    screenshot = NodeScreenshot.objects.create(node=node, path=str(path))
    return screenshot.path

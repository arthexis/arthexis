import logging
import socket
import os
from pathlib import Path

import pyperclip
from pyperclip import PyperclipException
from celery import shared_task

from .models import TextSample, Node, NodeScreenshot
from .utils import capture_screenshot

logger = logging.getLogger(__name__)


@shared_task
def sample_clipboard() -> None:
    """Save current clipboard contents to a :class:`TextSample` entry."""
    try:
        content = pyperclip.paste()
    except PyperclipException as exc:  # pragma: no cover - depends on OS clipboard
        logger.error("Clipboard error: %s", exc)
        return
    if not content:
        logger.info("Clipboard is empty")
        return
    if TextSample.objects.filter(content=content).exists():
        logger.info("Duplicate clipboard content; sample not created")
        return
    hostname = socket.gethostname()
    port = int(os.environ.get("PORT", 8000))
    node = Node.objects.filter(hostname=hostname, port=port).first()
    TextSample.objects.create(content=content, node=node, automated=True)


@shared_task
def capture_node_screenshot(url: str, port: int = 8000) -> str:
    """Capture a screenshot of ``url`` and record it as a :class:`NodeScreenshot`."""
    path: Path = capture_screenshot(url)
    hostname = socket.gethostname()
    node = Node.objects.filter(hostname=hostname, port=port).first()
    screenshot = NodeScreenshot.objects.create(node=node, path=str(path))
    return screenshot.path

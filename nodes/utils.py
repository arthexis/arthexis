from datetime import datetime
from pathlib import Path
import hashlib
import logging

from django.conf import settings
from selenium import webdriver
from selenium.webdriver.firefox.options import Options

from .models import NodeScreenshot

SCREENSHOT_DIR = settings.LOG_DIR / "screenshots"
logger = logging.getLogger(__name__)


def capture_screenshot(url: str) -> Path:
    """Capture a screenshot of ``url`` and save it to :data:`SCREENSHOT_DIR`."""
    options = Options()
    options.add_argument("-headless")
    with webdriver.Firefox(options=options) as browser:
        browser.set_window_size(1280, 720)
        browser.get(url)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        filename = SCREENSHOT_DIR / f"{datetime.utcnow():%Y%m%d%H%M%S}.png"
        browser.save_screenshot(str(filename))
    return filename


def save_screenshot(path: Path, node=None, method: str = ""):
    """Save screenshot file info if not already recorded.

    Returns the created :class:`NodeScreenshot` or ``None`` if duplicate.
    """

    original = path
    if not path.is_absolute():
        path = settings.LOG_DIR / path
    with path.open("rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    if NodeScreenshot.objects.filter(hash=digest).exists():
        logger.info("Duplicate screenshot content; record not created")
        return None
    stored_path = str(original if not original.is_absolute() else path)
    return NodeScreenshot.objects.create(
        node=node, path=stored_path, method=method, hash=digest
    )

from datetime import datetime
from pathlib import Path
import hashlib
import logging

from django.conf import settings
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.common.exceptions import WebDriverException

from .models import ContentSample

SCREENSHOT_DIR = settings.LOG_DIR / "screenshots"
logger = logging.getLogger(__name__)


def capture_screenshot(url: str, cookies=None) -> Path:
    """Capture a screenshot of ``url`` and save it to :data:`SCREENSHOT_DIR`.

    ``cookies`` can be an iterable of Selenium cookie mappings which will be
    applied after the initial navigation and before the screenshot is taken.
    """
    options = Options()
    options.add_argument("-headless")
    try:
        with webdriver.Firefox(options=options) as browser:
            browser.set_window_size(1280, 720)
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            filename = SCREENSHOT_DIR / f"{datetime.utcnow():%Y%m%d%H%M%S}.png"
            try:
                browser.get(url)
            except WebDriverException as exc:
                logger.error("Failed to load %s: %s", url, exc)
            if cookies:
                for cookie in cookies:
                    try:
                        browser.add_cookie(cookie)
                    except WebDriverException as exc:
                        logger.error("Failed to apply cookie for %s: %s", url, exc)
                browser.get(url)
            if not browser.save_screenshot(str(filename)):
                raise RuntimeError("Screenshot capture failed")
            return filename
    except WebDriverException as exc:
        logger.error("Failed to capture screenshot from %s: %s", url, exc)
        raise RuntimeError(f"Screenshot capture failed: {exc}") from exc


def save_screenshot(path: Path, node=None, method: str = "", transaction_uuid=None):
    """Save screenshot file info if not already recorded.

    Returns the created :class:`ContentSample` or ``None`` if duplicate.
    """

    original = path
    if not path.is_absolute():
        path = settings.LOG_DIR / path
    with path.open("rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    if ContentSample.objects.filter(hash=digest).exists():
        logger.info("Duplicate screenshot content; record not created")
        return None
    stored_path = (original if not original.is_absolute() else path).as_posix()
    data = {
        "node": node,
        "path": stored_path,
        "method": method,
        "hash": digest,
        "kind": ContentSample.IMAGE,
    }
    if transaction_uuid is not None:
        data["transaction_uuid"] = transaction_uuid
    return ContentSample.objects.create(**data)

from datetime import datetime
from pathlib import Path
import hashlib
import logging
import shutil
import subprocess
import uuid

from django.conf import settings
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.common.exceptions import WebDriverException

from .classifiers import run_default_classifiers, suppress_default_classifiers
from .models import ContentSample

SCREENSHOT_DIR = settings.LOG_DIR / "screenshots"
CAMERA_DIR = settings.LOG_DIR / "camera"
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


def capture_rpi_snapshot(timeout: int = 10) -> Path:
    """Capture a snapshot using the Raspberry Pi camera stack."""

    tool_path = shutil.which("rpicam-still")
    if not tool_path:
        raise RuntimeError("rpicam-still is not available")
    CAMERA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow()
    unique_suffix = uuid.uuid4().hex
    filename = CAMERA_DIR / f"{timestamp:%Y%m%d%H%M%S}-{unique_suffix}.jpg"
    try:
        result = subprocess.run(
            [tool_path, "-o", str(filename), "-t", "1"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover - depends on camera stack
        logger.error("Failed to invoke %s: %s", tool_path, exc)
        raise RuntimeError(f"Snapshot capture failed: {exc}") from exc
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "Snapshot capture failed").strip()
        logger.error("rpicam-still exited with %s: %s", result.returncode, error)
        raise RuntimeError(error)
    if not filename.exists():
        logger.error("Snapshot file %s was not created", filename)
        raise RuntimeError("Snapshot capture failed")
    return filename


def save_screenshot(
    path: Path,
    node=None,
    method: str = "",
    transaction_uuid=None,
    *,
    content: str | None = None,
    user=None,
):
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
    if content is not None:
        data["content"] = content
    if user is not None:
        data["user"] = user
    with suppress_default_classifiers():
        sample = ContentSample.objects.create(**data)
    run_default_classifiers(sample)
    return sample

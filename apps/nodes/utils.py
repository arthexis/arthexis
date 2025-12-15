from datetime import datetime
from pathlib import Path
import logging
import shutil
import subprocess

from django.conf import settings
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.common.exceptions import WebDriverException

try:  # pragma: no cover - optional dependency may be missing
    from geckodriver_autoinstaller import install as install_geckodriver
except Exception:  # pragma: no cover - fallback when installer is unavailable
    install_geckodriver = None

from apps.content.models import ContentSample
from apps.content.utils import save_content_sample

WORK_DIR = Path(settings.BASE_DIR) / "work"
SCREENSHOT_DIR = settings.LOG_DIR / "screenshots"
logger = logging.getLogger(__name__)

_FIREFOX_BINARY_CANDIDATES = ("firefox", "firefox-esr", "firefox-bin")


def _find_firefox_binary() -> str | None:
    """Return the first available Firefox binary path or ``None``."""

    for candidate in _FIREFOX_BINARY_CANDIDATES:
        path = shutil.which(candidate)
        if path:
            return path
    return None


def _ensure_geckodriver() -> None:
    """Install geckodriver on demand when possible."""

    if install_geckodriver is None:  # pragma: no cover - dependency not installed
        return
    try:
        install_geckodriver()
    except Exception as exc:  # pragma: no cover - external failures are rare in tests
        logger.warning("Unable to ensure geckodriver availability: %s", exc)


def capture_screenshot(url: str, cookies=None) -> Path:
    """Capture a screenshot of ``url`` and save it to :data:`SCREENSHOT_DIR`.

    ``cookies`` can be an iterable of Selenium cookie mappings which will be
    applied after the initial navigation and before the screenshot is taken.
    """
    firefox_binary = _find_firefox_binary()
    if not firefox_binary:
        raise RuntimeError(
            "Screenshot capture failed: Firefox is not installed. Install Firefox to enable screenshot capture."
        )

    options = Options()
    options.binary_location = firefox_binary
    options.add_argument("-headless")
    _ensure_geckodriver()
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
        message = str(exc)
        if "Unable to obtain driver for firefox" in message:
            message = (
                "Firefox WebDriver is unavailable. Install geckodriver or configure the GECKODRIVER environment variable so Selenium can locate it."
            )
        raise RuntimeError(f"Screenshot capture failed: {message}") from exc


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
    """Save screenshot file info if not already recorded.

    Returns the created :class:`ContentSample`. If ``link_duplicates`` is ``True``
    and a sample with identical content already exists, the existing record is
    returned instead of ``None``.
    """

    return save_content_sample(
        path=path,
        kind=ContentSample.IMAGE,
        node=node,
        method=method,
        transaction_uuid=transaction_uuid,
        user=user,
        link_duplicates=link_duplicates,
        content=content,
        duplicate_log_context="screenshot content",
    )



from __future__ import annotations

from datetime import datetime
from pathlib import Path
import hashlib
import logging
import platform

from django.conf import settings

from .classifiers import run_default_classifiers, suppress_default_classifiers
from .models import ContentSample

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency may be missing
    from PIL import ImageGrab
except Exception:  # pragma: no cover - fallback when dependency is unavailable
    ImageGrab = None

SCREENSHOT_DIR = settings.LOG_DIR / "screenshots"
DEFAULT_SCREENSHOT_RESOLUTION = (1280, 720)


def save_content_sample(
    *,
    path: Path,
    kind: str,
    node=None,
    method: str = "",
    transaction_uuid=None,
    user=None,
    link_duplicates: bool = False,
    content: str | None = None,
    duplicate_log_context: str,
):
    """Persist a :class:`ContentSample` if an identical hash is not present."""

    original = path
    if not path.is_absolute():
        path = settings.LOG_DIR / path
    with path.open("rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    existing = ContentSample.objects.filter(hash=digest).first()
    if existing:
        if link_duplicates:
            logger.info("Duplicate %s; reusing existing sample", duplicate_log_context)
            return existing
        logger.info("Duplicate %s; record not created", duplicate_log_context)
        return None
    stored_path = (original if not original.is_absolute() else path).as_posix()
    data = {
        "node": node,
        "path": stored_path,
        "method": method,
        "hash": digest,
        "kind": kind,
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


def _format_firefox_driver_help() -> str:
    """Return OS-aware instructions for installing geckodriver."""

    os_name = platform.system() or "Unknown"
    instructions: list[str] = [
        "Firefox WebDriver is unavailable.",
        f"Detected OS: {os_name}.",
    ]

    if os_name == "Linux":
        instructions.append(
            "Download geckodriver for Linux and place the executable in /usr/local/bin or /usr/bin, "
            "or set the GECKODRIVER environment variable to its full path. Ensure the file is executable (chmod +x)."
        )
    elif os_name == "Windows":
        instructions.append(
            "Download geckodriver.exe and add its folder (for example C:\\tools\\geckodriver) to the PATH or set the GECKODRIVER environment variable to the full executable path."
        )
    elif os_name == "Darwin":
        instructions.append(
            "Download the macOS geckodriver and place it in /usr/local/bin or another directory on PATH, or set GECKODRIVER to the executable path."
        )
    else:
        instructions.append(
            "Download the appropriate geckodriver for your platform and add it to PATH or set GECKODRIVER to the executable path."
        )

    instructions.append(
        "The suite runs headless Firefox; ensure Firefox itself is installed and available to the same user running the tests."
    )

    return " ".join(instructions)


def capture_screenshot(
    url: str,
    cookies=None,
    *,
    width: int | None = None,
    height: int | None = None,
) -> Path:
    """Capture a screenshot of ``url`` and save it to :data:`SCREENSHOT_DIR`.

    ``cookies`` can be an iterable of Selenium cookie mappings which will be
    applied after the initial navigation and before the screenshot is taken.
    """

    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.firefox.options import Options

    from apps.selenium.utils.firefox import ensure_geckodriver, find_firefox_binary

    firefox_binary = find_firefox_binary()
    if not firefox_binary:
        raise RuntimeError(
            "Screenshot capture failed: Firefox is not installed. Install Firefox to enable screenshot capture."
        )

    options = Options()
    options.binary_location = firefox_binary
    options.add_argument("-headless")
    ensure_geckodriver()
    resolution = (
        width or DEFAULT_SCREENSHOT_RESOLUTION[0],
        height or DEFAULT_SCREENSHOT_RESOLUTION[1],
    )

    try:
        with webdriver.Firefox(options=options) as browser:
            browser.set_window_size(*resolution)
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
            message = _format_firefox_driver_help()
        raise RuntimeError(f"Screenshot capture failed: {message}") from exc


def capture_local_screenshot() -> Path:
    """Capture a screenshot of the current screen and save it locally."""

    if ImageGrab is None:
        raise RuntimeError(
            "Local screenshot capture failed: Pillow is not installed. Install Pillow to enable local screenshots."
        )

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    filename = SCREENSHOT_DIR / f"{datetime.utcnow():%Y%m%d%H%M%S}.png"

    try:
        image = ImageGrab.grab()
    except Exception as exc:  # pragma: no cover - relies on system screenshot support
        raise RuntimeError(f"Local screenshot capture failed: {exc}") from exc

    image.save(filename)
    return filename


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
    """Capture a screenshot and persist it as a :class:`ContentSample`.

    When ``url`` is not provided and ``local`` is ``False``, the URL defaults to
    the local node using ``localhost`` and ``port``. Errors during capture can be
    logged and suppressed when ``log_capture_errors`` is ``True``.
    """

    from apps.nodes.models import Node

    node = Node.get_local()
    target_url = url

    if target_url is None and not local:
        scheme = node.get_preferred_scheme() if node else "http"
        target_url = f"{scheme}://localhost:{port}"

    try:
        if local:
            path = capture_local_screenshot()
        else:
            screenshot_kwargs = {}
            if width is not None:
                screenshot_kwargs["width"] = width
            if height is not None:
                screenshot_kwargs["height"] = height
            path = capture_screenshot(target_url, **screenshot_kwargs)
    except Exception as exc:
        if log_capture_errors and logger is not None:
            logger.error("Screenshot capture failed: %s", exc)
            return None
        raise

    save_screenshot(path, node=node, method=method)
    return path


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

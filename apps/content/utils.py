from __future__ import annotations

from datetime import datetime
from pathlib import Path
import hashlib
import logging
import os
import platform
import re
import subprocess

from django.conf import settings

from .classifiers import run_default_classifiers, suppress_default_classifiers
from .models import ContentSample
from apps.playwright.playwright import normalize_playwright_cookies

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency may be missing
    from PIL import ImageGrab
except Exception:  # pragma: no cover - fallback when dependency is unavailable
    ImageGrab = None

SCREENSHOT_DIR = settings.LOG_DIR / "screenshots"
DEFAULT_SCREENSHOT_RESOLUTION = (1280, 720)
_RUNSERVER_PORT_PATTERN = re.compile(r":(\d{2,5})(?:\D|$)")
_RUNSERVER_PORT_FLAG_PATTERN = re.compile(r"--port(?:=|\s+)(\d{2,5})", re.IGNORECASE)


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


def _format_playwright_help() -> str:
    """Return OS-aware instructions for installing Playwright browser binaries."""

    os_name = platform.system() or "Unknown"
    instructions: list[str] = [
        "Playwright browser runtime is unavailable.",
        f"Detected OS: {os_name}.",
        "Install Playwright browsers with `python -m playwright install chromium` and ensure the command runs for the same user executing the app.",
    ]
    return " ".join(instructions)


def capture_screenshot(
    url: str,
    cookies=None,
    *,
    width: int | None = None,
    height: int | None = None,
) -> Path:
    """Capture a screenshot of ``url`` and save it to :data:`SCREENSHOT_DIR`.

    ``cookies`` can be an iterable of cookie mappings compatible with Playwright
    ``BrowserContext.add_cookies``.
    """

    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    resolution = (
        width or DEFAULT_SCREENSHOT_RESOLUTION[0],
        height or DEFAULT_SCREENSHOT_RESOLUTION[1],
    )

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": resolution[0], "height": resolution[1]}
            )
            page = context.new_page()
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            filename = SCREENSHOT_DIR / f"{datetime.utcnow():%Y%m%d%H%M%S}.png"
            if cookies:
                normalized = normalize_playwright_cookies(cookies)
                if normalized:
                    context.add_cookies(normalized)
            try:
                page.goto(url, wait_until="networkidle")
            except PlaywrightError as exc:
                logger.error("Failed to load %s: %s", url, exc)
                raise
            page.screenshot(path=str(filename), full_page=True)
            context.close()
            browser.close()
            return filename
    except PlaywrightError as exc:
        logger.error("Failed to capture screenshot from %s: %s", url, exc)
        message = str(exc)
        if "Executable doesn't exist" in message:
            message = _format_playwright_help()
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
    port: int | None = None,
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
        port_value = _resolve_screenshot_port(port=port, node=node)
        target_url = f"{scheme}://localhost:{port_value}"

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


def _resolve_screenshot_port(*, port: int | None, node) -> int:
    if port is not None:
        return _normalize_port(port) or 8888
    if node is not None:
        node_port = _normalize_port(getattr(node, "port", None))
        if node_port is not None:
            return node_port
        return 8888
    env_port = _normalize_port(os.environ.get("PORT"))
    if env_port is not None:
        return env_port
    if settings.DEBUG:
        runserver_port = _detect_runserver_port()
        if runserver_port is not None:
            return runserver_port
    return 8888


def _normalize_port(value) -> int | None:
    if value is None:
        return None
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= port <= 65535:
        return port
    return None


def _detect_runserver_port() -> int | None:
    try:
        result = subprocess.run(
            ["pgrep", "-af", "manage.py runserver"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Error detecting runserver port: %s", exc)
        return None

    if result.returncode != 0:
        return None

    output = result.stdout.strip()
    if not output:
        return None

    for line in output.splitlines():
        for pattern in (_RUNSERVER_PORT_PATTERN, _RUNSERVER_PORT_FLAG_PATTERN):
            match = pattern.search(line)
            if match:
                return _normalize_port(match.group(1))
    return None


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

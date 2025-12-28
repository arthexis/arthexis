from __future__ import annotations

import logging
import shutil

try:  # pragma: no cover - optional dependency may be missing
    from geckodriver_autoinstaller import install as install_geckodriver
except Exception:  # pragma: no cover - fallback when installer is unavailable
    install_geckodriver = None

logger = logging.getLogger(__name__)

_FIREFOX_BINARY_CANDIDATES = ("firefox", "firefox-esr", "firefox-bin")


def find_firefox_binary(binary_path: str | None = None) -> str | None:
    """Return the first available Firefox binary path or ``None``.

    If ``binary_path`` is provided, it is returned directly.
    """

    if binary_path:
        return binary_path
    for candidate in _FIREFOX_BINARY_CANDIDATES:
        path = shutil.which(candidate)
        if path:
            return path
    return None


def ensure_geckodriver() -> None:
    """Install geckodriver on demand when possible."""

    if install_geckodriver is None:  # pragma: no cover - dependency not installed
        return
    try:  # pragma: no cover - external call
        install_geckodriver()
    except Exception as exc:  # pragma: no cover - external failures rare in tests
        logger.warning("Unable to ensure geckodriver availability: %s", exc)

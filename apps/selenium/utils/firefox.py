from __future__ import annotations

import shutil


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

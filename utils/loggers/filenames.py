"""Helpers for creating safe log filenames."""

from __future__ import annotations

import re

_FILENAME_SAFE_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


def normalize_log_filename(value: str, fallback: str = "arthexis") -> str:
    """Return a filename-safe string with nonstandard characters collapsed."""

    cleaned = _FILENAME_SAFE_PATTERN.sub("_", value.strip())
    cleaned = cleaned.strip("_.")
    return cleaned or fallback

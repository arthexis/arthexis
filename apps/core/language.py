"""Backward-compatible shims for language helpers now housed in ``apps.locale``."""

from apps.locale.language import (
    _available_language_codes,
    default_report_language,
    normalize_report_language,
    normalize_report_title,
)

__all__ = [
    "_available_language_codes",
    "default_report_language",
    "normalize_report_language",
    "normalize_report_title",
]

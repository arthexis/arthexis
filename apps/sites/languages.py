"""Language code helpers for public site URL handling."""

from __future__ import annotations

from django.conf import settings


def normalize_language_code(value: str | None) -> str:
    """Return the normalized two-letter language code when available."""

    if not isinstance(value, str):
        return ""

    short_code = value.split("-", maxsplit=1)[0].lower()
    if len(short_code) != 2:
        return ""
    return short_code


def get_supported_language_codes() -> set[str]:
    """Return the configured two-letter language prefixes used by public URLs."""

    return {
        short_code
        for code, _label in getattr(settings, "LANGUAGES", ())
        if (short_code := normalize_language_code(code))
    }

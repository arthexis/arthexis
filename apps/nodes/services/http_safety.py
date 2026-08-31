"""Small helpers for safe raw HTTP request construction."""

from __future__ import annotations

import re
from urllib.parse import quote

_HEADER_VALUE_RE = re.compile(r"^[A-Za-z0-9._~+/=:@%!\-\[\]]+$")


def has_ascii_control(value: str) -> bool:
    return any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)


def clean_http_header_value(value: object, *, default: str = "") -> str:
    text = str(value or "").strip()
    if not text or has_ascii_control(text):
        return default
    if not _HEADER_VALUE_RE.fullmatch(text):
        return default
    return text


def quote_http_request_path(value: object, *, default: str = "/") -> str:
    text = str(value or "").strip() or default
    if not text.startswith("/"):
        text = f"/{text}"
    return quote(text, safe="/")

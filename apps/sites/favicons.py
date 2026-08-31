from __future__ import annotations

import base64
import binascii
from functools import cache
from pathlib import Path

FAVICON_CONTENT_TYPE = "image/png"
FAVICON_DIR = Path(__file__).resolve().parent / "fixtures" / "data"
FAVICON_FILENAMES = {
    "default": "favicon.txt",
    "Watchtower": "favicon_watchtower.txt",
    "Constellation": "favicon_watchtower.txt",
    "Control": "favicon_control.txt",
    "Satellite": "favicon_satellite.txt",
}


@cache
def load_favicon_payload(filename: str) -> str:
    path = FAVICON_DIR / filename
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def load_favicon_data_uri(filename: str) -> str:
    payload = load_favicon_payload(filename)
    if not payload:
        return ""
    return f"data:{FAVICON_CONTENT_TYPE};base64,{payload}"


@cache
def load_favicon_bytes(filename: str) -> bytes:
    payload = load_favicon_payload(filename)
    if not payload:
        return b""
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return b""

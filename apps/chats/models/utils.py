from __future__ import annotations

import hashlib


def gravatar_url(email: str, *, size: int = 128, default: str = "identicon") -> str:
    """Return the Gravatar URL for the given email address."""

    normalized = (email or "").strip().lower()
    if not normalized:
        return ""
    digest = hashlib.md5(normalized.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"https://www.gravatar.com/avatar/{digest}?s={size}&d={default}"

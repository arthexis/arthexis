from __future__ import annotations

import contextlib
import time
from typing import Optional

from config.offline import network_available, requires_network

from .models import Package, ReleaseError

try:  # pragma: no cover - optional dependency
    import requests  # type: ignore
except Exception:  # pragma: no cover - fallback when missing
    requests = None  # type: ignore

_RETRYABLE_TWINE_ERRORS = (
    "connectionreseterror",
    "connection reset",
    "connection aborted",
    "protocolerror",
    "forcibly closed by the remote host",
    "remote host closed the connection",
    "temporary failure in name resolution",
)


def is_retryable_twine_error(output: str) -> bool:
    normalized = output.lower()
    return any(marker in normalized for marker in _RETRYABLE_TWINE_ERRORS)


def fetch_pypi_releases(
    package: Package,
    *,
    retries: int = 3,
    cooldown: float = 2.0,
) -> dict[str, object]:
    """Retrieve release metadata from the PyPI JSON API with retries."""

    if requests is None or not network_available():
        return {}

    url = f"https://pypi.org/pypi/{package.name}/json"
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        resp = None
        try:
            resp = requests.get(url, timeout=10)
            if resp.ok:
                return resp.json().get("releases", {})
            raise ReleaseError(
                f"PyPI JSON API returned status {resp.status_code} for '{package.name}'"
            )
        except ReleaseError:
            raise
        except Exception as exc:  # pragma: no cover - network failure
            last_error = exc
            if attempt < retries and is_retryable_twine_error(str(exc)):
                time.sleep(cooldown)
                continue
            raise ReleaseError(
                f"Failed to reach PyPI JSON API for '{package.name}': {exc}"
            ) from exc
        finally:
            if resp is not None:
                close = getattr(resp, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()

    if last_error is not None:
        raise ReleaseError(
            f"Failed to reach PyPI JSON API for '{package.name}': {last_error}"
        ) from last_error

    return {}


def close_response(resp: Optional[object]) -> None:
    if resp is None:
        return
    close = getattr(resp, "close", None)
    if callable(close):
        with contextlib.suppress(Exception):
            close()

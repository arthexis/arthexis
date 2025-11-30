from __future__ import annotations

import inspect
from functools import lru_cache
from collections.abc import Mapping
from typing import Any

import websockets


@lru_cache(maxsize=1)
def _header_param_name() -> str:
    """Return the keyword expected by ``websockets.connect`` for headers."""

    params = inspect.signature(websockets.connect).parameters
    if "additional_headers" in params:
        return "additional_headers"
    return "extra_headers"


def connect_headers_kwargs(headers: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    """Prepare kwargs for passing HTTP headers to ``websockets.connect``.

    The websockets library renamed the ``extra_headers`` keyword argument to
    ``additional_headers`` in version 15.  This helper inspects the installed
    version and returns a dictionary keyed with the appropriate name.  When no
    headers are supplied, an empty dictionary is returned so callers can unpack
    the result directly into ``websockets.connect``.
    """

    if not headers:
        return {}

    return {_header_param_name(): dict(headers)}


__all__ = ["connect_headers_kwargs"]

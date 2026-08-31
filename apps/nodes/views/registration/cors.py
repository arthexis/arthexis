"""CORS response policy for node registration endpoints."""

from __future__ import annotations

from django.conf import settings
from django.utils.cache import patch_vary_headers


def _allowed_origins() -> set[str]:
    """Return configured CORS allow-list for registration endpoints."""

    configured = getattr(settings, "VISITOR_CORS_ALLOWED_ORIGINS", ())
    if isinstance(configured, str):
        configured = (configured,)
    return {value.strip() for value in configured if value and value.strip()}


def add_cors_headers(request, response):
    """Attach CORS headers that match request preflight context."""

    origin = request.headers.get("Origin")
    allowed_origins = _allowed_origins()
    if origin and origin in allowed_origins:
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Credentials"] = "true"
        allow_headers = request.headers.get(
            "Access-Control-Request-Headers", "Content-Type"
        )
        response["Access-Control-Allow-Headers"] = allow_headers
        response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        patch_vary_headers(response, ["Origin"])
        return response

    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Credentials"] = "false"
    response["Access-Control-Allow-Headers"] = "Content-Type"
    response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return response

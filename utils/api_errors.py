from __future__ import annotations

from django.http import JsonResponse


def json_api_error(*, code: str, message: str, status: int) -> JsonResponse:
    """Return a stable machine-readable API error payload."""

    return JsonResponse(
        {
            "error": {
                "code": code,
                "message": message,
            }
        },
        status=status,
    )

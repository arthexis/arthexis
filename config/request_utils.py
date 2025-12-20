from __future__ import annotations

import logging
import os

_LOG_X_FORWARDED_PROTO = os.getenv("LOG_X_FORWARDED_PROTO", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_PROXY_HEADERS = (
    "HTTP_X_FORWARDED_FOR",
    "HTTP_X_FORWARDED_HOST",
    "HTTP_FORWARDED",
)
_logger = logging.getLogger("proxy_headers")


def _has_proxy_headers(request) -> bool:
    meta = getattr(request, "META", {})
    return any(meta.get(header) for header in _PROXY_HEADERS)


def _log_forwarded_proto_issue(request, message: str, value: str = "") -> None:
    if not _LOG_X_FORWARDED_PROTO:
        return
    _logger.warning(
        "%s (value=%s)",
        message,
        value,
    )


def is_https_request(request) -> bool:
    if request.is_secure():
        return True

    forwarded_proto = request.META.get("HTTP_X_FORWARDED_PROTO", "")
    if forwarded_proto:
        candidate = forwarded_proto.split(",")[0].strip().lower()
        if candidate == "https":
            return True
        if candidate:
            _log_forwarded_proto_issue(request, "Unexpected X-Forwarded-Proto header", candidate)
    elif _has_proxy_headers(request):
        _log_forwarded_proto_issue(request, "Missing X-Forwarded-Proto header")

    forwarded_header = request.META.get("HTTP_FORWARDED", "")
    for forwarded_part in forwarded_header.split(","):
        for element in forwarded_part.split(";"):
            key, _, value = element.partition("=")
            if key.strip().lower() == "proto" and value.strip().strip('"').lower() == "https":
                return True

    return False

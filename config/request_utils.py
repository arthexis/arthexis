from __future__ import annotations

from contextvars import ContextVar, Token
import logging
import os
from typing import Any
from uuid import uuid4

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
_REQUEST_LOG_CONTEXT: ContextVar[dict[str, str]] = ContextVar(
    "request_log_context",
    default={},
)
_CHARGER_KEYS = ("charger", "charger_id", "charge_box_id", "chargeBoxId", "chargerId")
_SESSION_KEYS = ("session", "session_id", "transaction_id", "transactionId")
_REQUEST_ID_HEADERS = (
    "HTTP_X_REQUEST_ID",
    "HTTP_X_CORRELATION_ID",
    "HTTP_X_AMZN_TRACE_ID",
)


def get_request_log_context() -> dict[str, str]:
    """Return the active request log context for the current execution context."""

    return _REQUEST_LOG_CONTEXT.get()


def set_request_log_context(request: Any, *, node_id: str = "") -> Token[dict[str, str]]:
    """Bind request identifiers into contextvars so logging filters can enrich records."""

    context = {
        "request_id": _extract_request_id(request),
        "node_id": str(node_id or ""),
        "charger_id": _extract_identifier(request, _CHARGER_KEYS),
        "session_id": _extract_identifier(request, _SESSION_KEYS),
    }
    return _REQUEST_LOG_CONTEXT.set(context)


def reset_request_log_context(token: Token[dict[str, str]]) -> None:
    """Restore request log context using a token returned by ``set_request_log_context``."""

    _REQUEST_LOG_CONTEXT.reset(token)


def _extract_identifier(request: Any, keys: tuple[str, ...]) -> str:
    resolver_match = getattr(request, "resolver_match", None)
    kwargs = getattr(resolver_match, "kwargs", {}) if resolver_match else {}
    for key in keys:
        value = kwargs.get(key)
        if value not in (None, ""):
            return str(value)

    for key in keys:
        value = request.GET.get(key)
        if value:
            return str(value)
    return ""


def _extract_request_id(request: Any) -> str:
    for header in _REQUEST_ID_HEADERS:
        value = request.META.get(header, "")
        if value:
            return str(value)

    for attr_name in ("request_id", "id"):
        value = getattr(request, attr_name, "")
        if value:
            return str(value)

    return str(uuid4())


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

from __future__ import annotations

from typing import Optional

from django.conf import settings


def resolve_ws_scheme(
    *,
    ws_scheme: Optional[str] = None,
    use_tls: Optional[bool] = None,
    request=None,
) -> str:
    """Return the websocket scheme based on explicit settings or site config."""

    if ws_scheme:
        normalized = ws_scheme.strip().lower()
        if "://" in normalized:
            normalized = normalized.split("://", 1)[0]
        if normalized in {"ws", "wss"}:
            return normalized
        if normalized in {"http", "https"}:
            return "wss" if normalized == "https" else "ws"

    if use_tls is not None:
        return "wss" if use_tls else "ws"

    if request is not None:
        try:
            from config.request_utils import is_https_request

            if is_https_request(request):
                return "wss"
        except Exception:
            pass

    protocol = _site_http_protocol()
    return "wss" if protocol == "https" else "ws"


def _site_http_protocol() -> str:
    try:
        from apps.nginx.models import SiteConfiguration

        config = SiteConfiguration.objects.filter(enabled=True).order_by("pk").first()
        if config:
            if not getattr(config, "external_websockets", True):
                return "http"
            if config.protocol:
                return str(config.protocol).strip().lower()
    except Exception:
        pass

    return str(getattr(settings, "DEFAULT_HTTP_PROTOCOL", "http")).strip().lower()

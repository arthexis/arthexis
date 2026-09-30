"""Request-origin logging for proxied Arthexis HTTP traffic."""

from ipaddress import ip_address
import logging


logger = logging.getLogger("arthexis.request")


def _is_loopback(value: str) -> bool:
    try:
        return ip_address(value).is_loopback
    except ValueError:
        return False


def client_ip(request) -> str:
    """Return the trustworthy originating client address for an HTTP request."""
    peer_ip = str(request.META.get("REMOTE_ADDR", "") or "").strip()
    if not _is_loopback(peer_ip):
        return peer_ip

    forwarded_for = str(request.META.get("HTTP_X_FORWARDED_FOR", "") or "")
    forwarded_ip = forwarded_for.split(",", 1)[0].strip()
    if forwarded_ip:
        return forwarded_ip

    real_ip = str(request.META.get("HTTP_X_REAL_IP", "") or "").strip()
    return real_ip or peer_ip


class RequestOriginLoggingMiddleware:
    """Log origin metadata before host validation or application dispatch."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        peer_ip = str(request.META.get("REMOTE_ADDR", "") or "").strip()
        logger.info(
            "http_request client_ip=%s peer_ip=%s method=%s host=%s path=%s",
            client_ip(request),
            peer_ip,
            request.method,
            str(request.META.get("HTTP_HOST", "") or ""),
            request.get_full_path(),
        )
        return self.get_response(request)

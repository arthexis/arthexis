import logging
import socket
from django.core.exceptions import DisallowedHost
from django.http import HttpResponsePermanentRedirect

from apps.nodes.models import Node
from utils.sites import get_site

from .active_app import set_active_app


class ActiveAppMiddleware:
    """Store the current app based on the request's site."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        site = get_site(request)
        node = Node.get_local()
        role_name = node.role.name if node and node.role else "Terminal"
        site_name = site.name if site else ""
        active = site_name or role_name
        set_active_app(active)
        request.site = site
        request.active_app = active
        try:
            response = self.get_response(request)
        finally:
            set_active_app(socket.gethostname())
        return response


def _is_https_request(request) -> bool:
    if request.is_secure():
        return True

    forwarded_proto = request.META.get("HTTP_X_FORWARDED_PROTO", "")
    if forwarded_proto:
        candidate = forwarded_proto.split(",")[0].strip().lower()
        if candidate == "https":
            return True

    forwarded_header = request.META.get("HTTP_FORWARDED", "")
    for forwarded_part in forwarded_header.split(","):
        for element in forwarded_part.split(";"):
            key, _, value = element.partition("=")
            if key.strip().lower() == "proto" and value.strip().strip('"').lower() == "https":
                return True

    return False


class SiteHttpsRedirectMiddleware:
    """Redirect HTTP traffic to HTTPS for sites that require it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        site = getattr(request, "site", None)
        if site is None:
            site = get_site(request)
            request.site = site

        if getattr(site, "require_https", False) and not _is_https_request(request):
            try:
                host = request.get_host()
            except DisallowedHost:  # pragma: no cover - defensive guard
                host = request.META.get("HTTP_HOST", "")
            redirect_url = f"https://{host}{request.get_full_path()}"
            return HttpResponsePermanentRedirect(redirect_url)

        return self.get_response(request)


class ContentSecurityPolicyMiddleware:
    """Apply CSP headers to HTTPS responses."""

    header_value = "upgrade-insecure-requests; block-all-mixed-content"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if _is_https_request(request):
            response["Content-Security-Policy"] = self.header_value
        return response


class PageMissLoggingMiddleware:
    """Log 404 and 500 responses to a dedicated file handler."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.logger = logging.getLogger("page_misses")

    def __call__(self, request):
        try:
            response = self.get_response(request)
        except Exception:
            self._log_page_miss(request, 500)
            raise

        self._maybe_log_response(request, response)
        return response

    def _maybe_log_response(self, request, response) -> None:
        if response.status_code in (404, 500):
            self._log_page_miss(request, response.status_code)

    def _log_page_miss(self, request, status_code: int) -> None:
        path = request.get_full_path() if hasattr(request, "get_full_path") else str(request)
        self.logger.info("%s %s -> %s", request.method, path, status_code)

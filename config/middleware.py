import socket
import logging
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


class PageMissLoggingMiddleware:
    """Log requests that result in 404 or 500 responses."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.logger = logging.getLogger("page_misses")

    def __call__(self, request):
        try:
            response = self.get_response(request)
        except Exception:
            self.logger.warning(self._build_message(request, 500), exc_info=True)
            raise

        self._log_if_page_miss(request, response)
        return response

    def _log_if_page_miss(self, request, response) -> None:
        status_code = getattr(response, "status_code", 0)
        if status_code not in (404, 500):
            return

        self.logger.warning(self._build_message(request, status_code))

    def _build_message(self, request, status_code: int) -> str:
        method = getattr(request, "method", "").upper() or "UNKNOWN"
        path_getter = getattr(request, "get_full_path", None)
        if callable(path_getter):
            path = path_getter()
        else:
            path = getattr(request, "path", "")
        return f"{method} {path} -> {status_code}"

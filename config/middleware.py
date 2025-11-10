import socket
import sys

from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.http import Http404, HttpResponsePermanentRedirect
from django.utils.deprecation import MiddlewareMixin
from django.views.debug import technical_404_response, technical_500_response

from nodes.models import Node
from utils.sites import get_site

from .active_app import set_active_app


class DebugFriendlyErrorMiddleware(MiddlewareMixin):
    """Render Django's technical error pages when debugging is enabled."""

    def process_exception(self, request, exception):
        if not settings.DEBUG:
            return None

        if isinstance(exception, Http404):
            return technical_404_response(request, exception)

        exc_type, exc_value, exc_tb = sys.exc_info()
        if exc_type is None:
            exc_type = type(exception)
            exc_value = exception
            exc_tb = exception.__traceback__
        return technical_500_response(request, exc_type, exc_value, exc_tb)


class ActiveAppMiddleware:
    """Store the current app based on the request's site."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        site = get_site(request)
        node = Node.get_local()
        role_name = node.role.name if node and node.role else "Terminal"
        active = site.name or role_name
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

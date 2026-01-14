import logging
import socket
from http import HTTPStatus
from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.http import HttpResponsePermanentRedirect
from django.http.request import split_domain_port
from django.urls import Resolver404, resolve

from apps.core.analytics import record_request_event
from apps.core.models import UsageEvent
from apps.nodes.models import Node
from utils.sites import get_site

from .active_app import set_active_app
from .request_utils import is_https_request

_is_https_request = is_https_request


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


class SiteHttpsRedirectMiddleware:
    """Redirect HTTP traffic to HTTPS for sites that require it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        site = getattr(request, "site", None)
        if site is None:
            site = get_site(request)
            request.site = site

        if getattr(site, "require_https", False) and not is_https_request(request):
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


class CrossOriginOpenerPolicyMiddleware:
    """Strip COOP headers on non-trustworthy HTTP origins."""

    header_name = "Cross-Origin-Opener-Policy"
    trusted_hosts = {"localhost", "127.0.0.1", "::1"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if self.header_name in response and not self._is_trustworthy(request):
            del response[self.header_name]
        return response

    def _is_trustworthy(self, request) -> bool:
        if _is_https_request(request):
            return True
        try:
            host = request.get_host()
        except DisallowedHost:  # pragma: no cover - defensive guard
            host = request.META.get("HTTP_HOST", "")
        host, _ = split_domain_port(host)
        host = (host or "").lower()
        return host in self.trusted_hosts


class UsageAnalyticsMiddleware:
    """Record request-level usage events for reporting."""

    def __init__(self, get_response):
        self.get_response = get_response
        self._excluded_prefixes = self._resolve_excluded_prefixes()
        static_url = getattr(settings, "STATIC_URL", "") or ""
        media_url = getattr(settings, "MEDIA_URL", "") or ""
        self._skipped_prefixes = tuple(
            prefix.rstrip("/") for prefix in (static_url, media_url) if prefix
        )

    def __call__(self, request):
        if not getattr(settings, "ENABLE_USAGE_ANALYTICS", False):
            return self.get_response(request)

        if not self._should_track(request):
            return self.get_response(request)

        try:
            response = self.get_response(request)
        except Exception as exc:
            status_code = getattr(exc, "status_code", 500) or 500
            self._record_event(request, status_code, error_message=str(exc))
            raise
        else:
            status_code = getattr(response, "status_code", 0) or 0
            self._record_event(request, status_code)
            return response

    def _resolve_excluded_prefixes(self):
        return getattr(settings, "ANALYTICS_EXCLUDED_URL_PREFIXES", ())

    def _should_track(self, request) -> bool:
        method = request.method.upper()
        if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}:
            return False

        path = request.path
        if any(path.startswith(prefix) for prefix in self._excluded_prefixes):
            return False

        if any(path.startswith(prefix) for prefix in self._skipped_prefixes):
            return False

        if path.startswith("/favicon") or path.startswith("/robots.txt"):
            return False

        if "djdt" in request.GET:
            return False

        return True

    def _record_event(self, request, status_code: int, error_message: str = "") -> None:
        match = getattr(request, "resolver_match", None) or self._resolve_match(request)
        view_name, module = self._resolve_view_name(match)
        app_label = self._derive_app_label(module)
        action = self._resolve_action(request.method)
        metadata = {}
        if error_message:
            metadata["error"] = error_message
        try:
            status = HTTPStatus(status_code)
            metadata["status_text"] = status.phrase
        except ValueError:
            pass

        record_request_event(
            user=getattr(request, "user", None),
            app_label=app_label,
            view_name=view_name,
            path=request.get_full_path(),
            method=request.method,
            status_code=status_code,
            action=action,
            metadata=metadata,
        )

    def _resolve_match(self, request):
        try:
            return resolve(request.path_info)
        except Resolver404:
            return None

    def _resolve_view_name(self, match) -> tuple[str, str]:
        if match is None:
            return "", ""

        if getattr(match, "view_name", ""):
            func = getattr(match, "func", None)
            module = getattr(func, "__module__", "")
            return match.view_name, module

        func = getattr(match, "func", None)
        if func is None:
            return "", ""

        module = getattr(func, "__module__", "")
        name = getattr(func, "__name__", "")
        if module and name:
            return f"{module}.{name}", module
        return name or module or "", module

    def _derive_app_label(self, module_path: str) -> str:
        if not module_path:
            return ""
        parts = module_path.split(".")
        if parts and parts[0] == "apps" and len(parts) > 1:
            return parts[1]
        return parts[0]

    def _resolve_action(self, method: str) -> str:
        normalized = method.upper()
        if normalized in {"POST", "PUT", "PATCH"}:
            return UsageEvent.Action.CREATE if normalized == "POST" else UsageEvent.Action.UPDATE
        if normalized == "DELETE":
            return UsageEvent.Action.DELETE
        return UsageEvent.Action.READ


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

"""Middleware helpers for the pages application."""

from __future__ import annotations

import ipaddress
import logging
from http import HTTPStatus

from django.conf import settings
from django.urls import Resolver404, resolve

from .models import Landing, LandingLead, ViewHistory
from .utils import (
    cache_original_referer,
    get_original_referer,
    get_request_language_code,
    landing_leads_supported,
)


logger = logging.getLogger(__name__)


class LanguagePreferenceMiddleware:
    """Attach the active interface language to the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language_code = get_request_language_code(request)
        request.selected_language_code = language_code
        request.selected_language = language_code
        return self.get_response(request)


class ViewHistoryMiddleware:
    """Persist public site visits for analytics."""

    _EXCLUDED_PREFIXES = ("/admin", "/__debug__", "/healthz", "/status")

    def __init__(self, get_response):
        self.get_response = get_response
        static_url = getattr(settings, "STATIC_URL", "") or ""
        media_url = getattr(settings, "MEDIA_URL", "") or ""
        self._skipped_prefixes = tuple(
            prefix.rstrip("/") for prefix in (static_url, media_url) if prefix
        )

    def __call__(self, request):
        cache_original_referer(request)
        should_track = self._should_track(request)
        if not should_track:
            return self.get_response(request)

        error_message = ""
        try:
            response = self.get_response(request)
        except Exception as exc:  # pragma: no cover - re-raised for Django
            status_code = getattr(exc, "status_code", 500) or 500
            error_message = str(exc)
            self._record_visit(request, status_code, error_message)
            raise
        else:
            status_code = getattr(response, "status_code", 0) or 0
            self._record_visit(request, status_code, error_message)
            return response

    def _should_track(self, request) -> bool:
        method = request.method.upper()
        if method not in {"GET", "HEAD"}:
            return False

        path = request.path
        if any(path.startswith(prefix) for prefix in self._EXCLUDED_PREFIXES):
            return False

        if any(path.startswith(prefix) for prefix in self._skipped_prefixes):
            return False

        if path.startswith("/favicon") or path.startswith("/robots.txt"):
            return False

        if "djdt" in request.GET:
            return False

        return True

    def _record_visit(self, request, status_code: int, error_message: str) -> None:
        try:
            status = HTTPStatus(status_code)
            status_text = status.phrase
        except ValueError:
            status_text = ""

        view_name = self._resolve_view_name(request)
        full_path = request.get_full_path()
        if not error_message and status_code >= HTTPStatus.BAD_REQUEST:
            error_message = status_text or f"HTTP {status_code}"

        landing = None
        if status_code < HTTPStatus.BAD_REQUEST:
            landing = self._resolve_landing(request)

        try:
            ViewHistory.objects.create(
                path=full_path,
                method=request.method,
                status_code=status_code,
                status_text=status_text,
                error_message=(error_message or "")[:1000],
                view_name=view_name,
            )
        except Exception:  # pragma: no cover - best effort logging
            logger.debug(
                "Failed to record ViewHistory for %s", full_path, exc_info=True
            )
        else:
            self._update_user_last_visit_ip(request)

        if landing is not None:
            self._record_landing_lead(request, landing)

    def _resolve_landing(self, request):
        path = request.path
        if not path:
            return None
        try:
            return (
                Landing.objects.filter(
                    path=path,
                    enabled=True,
                    is_deleted=False,
                )
                .select_related("module", "module__application", "module__node_role")
                .first()
            )
        except Exception:  # pragma: no cover - best effort logging
            logger.debug(
                "Failed to resolve Landing for %s", path, exc_info=True
            )
            return None

    def _record_landing_lead(self, request, landing):
        if request.method.upper() != "GET":
            return

        if not getattr(landing, "track_leads", False):
            return

        if not landing_leads_supported():
            return

        referer = get_original_referer(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "") or ""
        ip_address = self._extract_client_ip(request) or None
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            user = None

        try:
            LandingLead.objects.create(
                landing=landing,
                user=user,
                path=request.get_full_path(),
                referer=referer,
                user_agent=user_agent,
                ip_address=ip_address,
            )
        except Exception:  # pragma: no cover - best effort logging
            logger.debug(
                "Failed to record LandingLead for %s", landing.path, exc_info=True
            )

    def _resolve_view_name(self, request) -> str:
        match = getattr(request, "resolver_match", None)
        if match is None:
            try:
                match = resolve(request.path_info)
            except Resolver404:
                return ""

        if getattr(match, "view_name", ""):
            return match.view_name

        func = getattr(match, "func", None)
        if func is None:
            return ""

        module = getattr(func, "__module__", "")
        name = getattr(func, "__name__", "")
        if module and name:
            return f"{module}.{name}"
        return name or module or ""

    def _extract_client_ip(self, request) -> str:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        candidates = []
        if forwarded:
            candidates.extend(part.strip() for part in forwarded.split(","))
        remote = request.META.get("REMOTE_ADDR", "").strip()
        if remote:
            candidates.append(remote)

        for candidate in candidates:
            if not candidate:
                continue
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                continue
            return candidate
        return ""

    def _update_user_last_visit_ip(self, request) -> None:
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
            return

        ip_address = self._extract_client_ip(request)
        if not ip_address or getattr(user, "last_visit_ip_address", None) == ip_address:
            return

        try:
            user.last_visit_ip_address = ip_address
            user.save(update_fields=["last_visit_ip_address"])
        except Exception:  # pragma: no cover - best effort logging
            logger.debug(
                "Failed to update last_visit_ip_address for user %s", user.pk,
                exc_info=True,
            )

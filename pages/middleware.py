"""Middleware helpers for the pages application."""

from __future__ import annotations

import logging
from http import HTTPStatus

from django.conf import settings
from django.urls import Resolver404, resolve

from .models import ViewHistory


logger = logging.getLogger(__name__)


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

        return True

    def _record_visit(self, request, status_code: int, error_message: str) -> None:
        try:
            status = HTTPStatus(status_code)
            status_text = status.phrase
        except ValueError:
            status_text = ""

        view_name = self._resolve_view_name(request)
        full_path = request.get_full_path()
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
            logger.debug("Failed to record ViewHistory for %s", full_path, exc_info=True)

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


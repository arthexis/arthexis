"""Diagnostics-focused admin actions for charge points."""

from __future__ import annotations

from functools import wraps
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from apps.ocpp.admin.charge_point.admin import ChargerAdmin as _RegisteredChargerAdmin


class DiagnosticsActionsMixin:
    """Delegate diagnostics workflows to the registered charger admin behavior."""

    DiagnosticsDownloadError = _RegisteredChargerAdmin.DiagnosticsDownloadError
    _SAFE_DIAGNOSTICS_EXTENSIONS = {".log", ".txt", ".zip", ".json", ".csv"}

    def _diagnostics_directory_for(self, user):
        return super()._diagnostics_directory_for(user)

    def _content_disposition_filename(self, header_value: str) -> str:
        return super()._content_disposition_filename(header_value)

    def _diagnostics_filename(self, charger, location: str, response) -> str:
        filename = super()._diagnostics_filename(charger, location, response)
        suffix = ''.join(Path(filename).suffixes).lower()
        if suffix not in self._SAFE_DIAGNOSTICS_EXTENSIONS:
            raise self.DiagnosticsDownloadError(
                f"Diagnostics file extension '{suffix or '<none>'}' is not allowed."
            )
        return filename

    def _unique_diagnostics_path(self, directory, filename: str):
        return super()._unique_diagnostics_path(directory, filename)

    def _validate_diagnostics_location(self, location: str) -> None:
        parsed = urlparse(location)
        host = (parsed.hostname or '').strip().lower()
        if not host:
            raise self.DiagnosticsDownloadError('Diagnostics location host is required.')

        allowed_hosts = {
            host.lower()
            for host in getattr(settings, 'OCPP_DIAGNOSTICS_ALLOWED_HOSTS', [])
            if host
        }
        if allowed_hosts and host not in allowed_hosts:
            raise self.DiagnosticsDownloadError('Diagnostics host is not in the allow-list.')

        try:
            ip = ip_address(host)
        except ValueError:
            return

        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise self.DiagnosticsDownloadError('Diagnostics location resolves to a disallowed IP address.')

    def _download_diagnostics(self, request, charger, location: str, diagnostics_dir, user_dir):
        self._validate_diagnostics_location(location)
        destination, asset_url = super()._download_diagnostics(
            request,
            charger,
            location,
            diagnostics_dir,
            user_dir,
        )
        try:
            destination.relative_to(Path(settings.BASE_DIR) / 'work')
        except ValueError:
            raise self.DiagnosticsDownloadError('Diagnostics path escaped work directory.')
        return destination, asset_url

    def _prepare_diagnostics_payload(self, request, charger, *, expires_at):
        return super()._prepare_diagnostics_payload(
            request,
            charger,
            expires_at=expires_at,
        )

    def _request_get_diagnostics(self, request, queryset, *, expires_at, success_message):
        return super()._request_get_diagnostics(
            request,
            queryset,
            expires_at=expires_at,
            success_message=success_message,
        )

    @wraps(_RegisteredChargerAdmin.request_cp_diagnostics)
    def request_cp_diagnostics(self, request, queryset):
        return super().request_cp_diagnostics(request, queryset)

    @wraps(_RegisteredChargerAdmin.setup_cp_diagnostics)
    def setup_cp_diagnostics(self, request, queryset):
        return super().setup_cp_diagnostics(request, queryset)

    @wraps(_RegisteredChargerAdmin.get_diagnostics)
    def get_diagnostics(self, request, queryset):
        return super().get_diagnostics(request, queryset)

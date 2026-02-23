"""Diagnostics-focused admin actions for charge points."""

from __future__ import annotations

from apps.ocpp.admin.charge_point.admin import ChargerAdmin as _RegisteredChargerAdmin


class DiagnosticsActionsMixin:
    """Delegate diagnostics workflows to the registered charger admin behavior."""

    DiagnosticsDownloadError = _RegisteredChargerAdmin.DiagnosticsDownloadError

    def _diagnostics_directory_for(self, user):
        return _RegisteredChargerAdmin._diagnostics_directory_for(self, user)

    def _content_disposition_filename(self, header_value: str) -> str:
        return _RegisteredChargerAdmin._content_disposition_filename(self, header_value)

    def _diagnostics_filename(self, charger, location: str, response) -> str:
        return _RegisteredChargerAdmin._diagnostics_filename(self, charger, location, response)

    def _unique_diagnostics_path(self, directory, filename: str):
        return _RegisteredChargerAdmin._unique_diagnostics_path(self, directory, filename)

    def _download_diagnostics(self, request, charger, location: str, diagnostics_dir, user_dir):
        return _RegisteredChargerAdmin._download_diagnostics(
            self,
            request,
            charger,
            location,
            diagnostics_dir,
            user_dir,
        )

    def _prepare_diagnostics_payload(self, request, charger, *, expires_at):
        return _RegisteredChargerAdmin._prepare_diagnostics_payload(
            self,
            request,
            charger,
            expires_at=expires_at,
        )

    def _request_get_diagnostics(self, request, queryset, *, expires_at, success_message):
        return _RegisteredChargerAdmin._request_get_diagnostics(
            self,
            request,
            queryset,
            expires_at=expires_at,
            success_message=success_message,
        )

    def request_cp_diagnostics(self, request, queryset):
        return _RegisteredChargerAdmin.request_cp_diagnostics(self, request, queryset)

    def setup_cp_diagnostics(self, request, queryset):
        return _RegisteredChargerAdmin.setup_cp_diagnostics(self, request, queryset)

    def get_diagnostics(self, request, queryset):
        return _RegisteredChargerAdmin.get_diagnostics(self, request, queryset)

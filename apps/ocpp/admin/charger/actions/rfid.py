"""RFID-focused admin actions for charge points."""

from __future__ import annotations

from apps.ocpp.admin.charge_point.admin import ChargerAdmin as _RegisteredChargerAdmin


class RFIDActionsMixin:
    """Delegate RFID action behavior to the registered charger admin."""

    def _build_local_authorization_list(self, charger):
        return _RegisteredChargerAdmin._build_local_authorization_list(self, charger)

    def toggle_rfid_authentication(self, request, queryset):
        return _RegisteredChargerAdmin.toggle_rfid_authentication(self, request, queryset)

    def send_rfid_list_to_evcs(self, request, queryset):
        return _RegisteredChargerAdmin.send_rfid_list_to_evcs(self, request, queryset)

    def update_rfids_from_evcs(self, request, queryset):
        return _RegisteredChargerAdmin.update_rfids_from_evcs(self, request, queryset)

    def clear_authorization_cache(self, request, queryset):
        return _RegisteredChargerAdmin.clear_authorization_cache(self, request, queryset)

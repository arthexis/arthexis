"""RFID-focused admin actions for charge points."""

from __future__ import annotations



class RFIDActionsMixin:
    """Delegate RFID action behavior to the registered charger admin."""

    def _build_local_authorization_list(self):
        return super()._build_local_authorization_list()

    def toggle_rfid_authentication(self, request, queryset):
        return super().toggle_rfid_authentication(request, queryset)

    def send_rfid_list_to_evcs(self, request, queryset):
        return super().send_rfid_list_to_evcs(request, queryset)

    def update_rfids_from_evcs(self, request, queryset):
        return super().update_rfids_from_evcs(request, queryset)

    def clear_authorization_cache(self, request, queryset):
        return super().clear_authorization_cache(request, queryset)

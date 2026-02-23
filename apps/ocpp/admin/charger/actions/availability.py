"""Availability-focused admin actions for charge points."""

from __future__ import annotations

from apps.ocpp.admin.charge_point.admin import ChargerAdmin as _RegisteredChargerAdmin


class AvailabilityActionsMixin:
    """Delegate availability operations to the registered charger admin behavior."""

    def _dispatch_change_availability(self, request, queryset, *, availability_type: str):
        return _RegisteredChargerAdmin._dispatch_change_availability(
            self,
            request,
            queryset,
            availability_type=availability_type,
        )

    def change_availability_operative(self, request, queryset):
        return _RegisteredChargerAdmin.change_availability_operative(self, request, queryset)

    def change_availability_inoperative(self, request, queryset):
        return _RegisteredChargerAdmin.change_availability_inoperative(self, request, queryset)

    def _set_availability_state(self, request, queryset, *, target_state, label):
        return _RegisteredChargerAdmin._set_availability_state(
            self,
            request,
            queryset,
            target_state=target_state,
            label=label,
        )

    def set_availability_state_operative(self, request, queryset):
        return _RegisteredChargerAdmin.set_availability_state_operative(self, request, queryset)

    def set_availability_state_inoperative(self, request, queryset):
        return _RegisteredChargerAdmin.set_availability_state_inoperative(self, request, queryset)

    def _charger_availability_state(self, charger):
        return _RegisteredChargerAdmin._charger_availability_state(self, charger)

    def _charger_availability_timestamp(self, charger):
        return _RegisteredChargerAdmin._charger_availability_timestamp(self, charger)

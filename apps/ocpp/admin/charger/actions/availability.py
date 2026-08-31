"""Availability-focused admin actions for charge points."""

from __future__ import annotations

from functools import wraps

from apps.ocpp.admin.charge_point.admin import ChargerAdmin as _RegisteredChargerAdmin


class AvailabilityActionsMixin:
    """Delegate availability operations to the registered charger admin behavior."""

    def _dispatch_change_availability(self, request, queryset, availability_type: str):
        return super()._dispatch_change_availability(request, queryset, availability_type)

    @wraps(_RegisteredChargerAdmin.change_availability_operative)
    def change_availability_operative(self, request, queryset):
        return super().change_availability_operative(request, queryset)

    @wraps(_RegisteredChargerAdmin.change_availability_inoperative)
    def change_availability_inoperative(self, request, queryset):
        return super().change_availability_inoperative(request, queryset)

    def _set_availability_state(self, request, queryset, availability_state: str):
        return super()._set_availability_state(request, queryset, availability_state)

    @wraps(_RegisteredChargerAdmin.set_availability_state_operative)
    def set_availability_state_operative(self, request, queryset):
        return super().set_availability_state_operative(request, queryset)

    @wraps(_RegisteredChargerAdmin.set_availability_state_inoperative)
    def set_availability_state_inoperative(self, request, queryset):
        return super().set_availability_state_inoperative(request, queryset)

    def _charger_availability_state(self, charger):
        return super()._charger_availability_state(charger)

    def _charger_availability_timestamp(self, charger):
        return super()._charger_availability_timestamp(charger)

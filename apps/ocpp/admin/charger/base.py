"""Compatibility charger admin shell structured as a package."""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import path, reverse

from apps.ocpp.admin.charge_point.admin import ChargerAdmin as _RegisteredChargerAdmin

from .actions import (
    AvailabilityActionsMixin,
    DiagnosticsActionsMixin,
    RFIDActionsMixin,
    TransactionsActionsMixin,
)
from .queryset import ChargerQuerysetMixin
from .tariffs import ChargerTariffMixin


class ChargerAdmin(
    DiagnosticsActionsMixin,
    AvailabilityActionsMixin,
    RFIDActionsMixin,
    TransactionsActionsMixin,
    ChargerQuerysetMixin,
    ChargerTariffMixin,
    _RegisteredChargerAdmin,
):
    """Packaged charger admin exposing the same action surface and URLs."""

    fieldsets = _RegisteredChargerAdmin.fieldsets
    readonly_fields = _RegisteredChargerAdmin.readonly_fields
    list_display = _RegisteredChargerAdmin.list_display
    actions = list(_RegisteredChargerAdmin.actions)

    @admin.action(description="View in Site")
    def view_charge_point_dashboard(self, request, queryset=None):
        """Redirect users to the OCPP dashboard from the action dropdown."""

        return HttpResponseRedirect(reverse("ocpp:ocpp-dashboard"))

    def get_urls(self):
        """Expose the custom admin endpoint used by the changelist action."""

        urls = super().get_urls()
        custom = [
            path(
                "view-in-site/",
                self.admin_site.admin_view(self.view_charge_point_dashboard),
                name="ocpp_charger_view_charge_point_dashboard",
            ),
        ]
        return custom + urls

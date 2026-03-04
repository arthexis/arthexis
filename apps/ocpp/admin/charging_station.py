"""Admin registration for charging stations and station-level actions."""

from django.contrib import admin

from apps.core.admin import OwnableAdminMixin
from apps.locals.user_data import EntityModelAdmin

from ..models import Charger, ChargingStation
from .charge_point.actions.authorization import AuthorizationActionsMixin


@admin.register(ChargingStation)
class ChargingStationAdmin(AuthorizationActionsMixin, OwnableAdminMixin, EntityModelAdmin):
    """Expose station-level commands that target all station charge points."""

    list_display = ("station_id", "display_name", "last_heartbeat", "location")
    search_fields = ("station_id", "display_name", "location__name")
    actions = [
        "fetch_station_configuration",
        "toggle_station_rfid_authentication",
        "send_station_rfid_list_to_evcs",
        "update_station_rfids_from_evcs",
        "clear_station_authorization_cache",
        "clear_station_charging_profiles",
    ]

    def _station_charge_points(self, station_queryset):
        """Return charge-point rows linked to selected stations."""
        return Charger.objects.filter(charging_station__in=station_queryset)

    @admin.action(description="Fetch station configuration")
    def fetch_station_configuration(self, request, queryset):
        """Request GetConfiguration for all selected stations' charge points."""

        return self.fetch_cp_configuration(request, self._station_charge_points(queryset))

    @admin.action(description="Toggle station RFID authentication")
    def toggle_station_rfid_authentication(self, request, queryset):
        """Toggle RFID auth for all selected stations' charge points."""

        return self.toggle_rfid_authentication(request, self._station_charge_points(queryset))

    @admin.action(description="Send local RFIDs to selected stations")
    def send_station_rfid_list_to_evcs(self, request, queryset):
        """Push local RFID list to all selected stations' charge points."""

        return self.send_rfid_list_to_evcs(request, self._station_charge_points(queryset))

    @admin.action(description="Update RFIDs from selected stations")
    def update_station_rfids_from_evcs(self, request, queryset):
        """Fetch local-list version from all selected stations' charge points."""

        return self.update_rfids_from_evcs(request, self._station_charge_points(queryset))

    @admin.action(description="Clear authorization cache on selected stations")
    def clear_station_authorization_cache(self, request, queryset):
        """Clear auth cache on all selected stations' charge points."""

        return self.clear_authorization_cache(request, self._station_charge_points(queryset))

    @admin.action(description="Clear charging profiles on selected stations")
    def clear_station_charging_profiles(self, request, queryset):
        """Clear charging profiles on all selected stations' charge points."""

        return self.clear_charging_profiles(request, self._station_charge_points(queryset))

"""Admin registration for charging stations and station-level actions."""

from django import forms
from django.contrib import admin

from apps.core.admin import OwnableAdminMixin
from apps.locale.models import Language
from apps.locals.user_data import EntityModelAdmin

from ..models import Charger, ChargingStation
from .charge_point.actions.authorization import AuthorizationActionsMixin


class ChargingStationAdminForm(forms.ModelForm):
    """Expose station-managed charge-point settings on the station admin form."""

    public_display = forms.BooleanField(required=False)
    language = forms.ModelChoiceField(queryset=Language.objects.none(), required=False)
    preferred_ocpp_version = forms.CharField(required=False, max_length=16)
    energy_unit = forms.ChoiceField(choices=Charger.EnergyUnit.choices)
    authorization_policy = forms.ChoiceField(
        choices=Charger.AuthorizationPolicy.choices,
        required=False,
    )

    class Meta:
        model = ChargingStation
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        """Prefill station-managed charge-point settings from linked charge points."""

        super().__init__(*args, **kwargs)
        self.fields["public_display"].initial = True
        self.fields["energy_unit"].initial = Charger.EnergyUnit.KW
        self.fields["language"].queryset = Language.objects.order_by("code")

        instance = kwargs.get("instance")
        if not instance:
            return

        root_cp = (
            Charger.objects.filter(charging_station=instance, connector_id__isnull=True)
            .order_by("pk")
            .first()
        )
        if not root_cp:
            return

        self.fields["public_display"].initial = root_cp.public_display
        self.fields["language"].initial = root_cp.language_id
        self.fields["preferred_ocpp_version"].initial = root_cp.preferred_ocpp_version
        self.fields["energy_unit"].initial = root_cp.energy_unit
        self.fields["authorization_policy"].initial = root_cp.authorization_policy


@admin.register(ChargingStation)
class ChargingStationAdmin(AuthorizationActionsMixin, OwnableAdminMixin, EntityModelAdmin):
    """Expose station-level commands that target all station charge points."""

    form = ChargingStationAdminForm
    list_display = ("station_id", "display_name", "last_heartbeat", "location")
    search_fields = ("station_id", "display_name", "location__name")
    fieldsets = (
        (
            "General",
            {
                "fields": (
                    "station_id",
                    "display_name",
                    "location",
                    "station_model",
                    "last_path",
                    "last_heartbeat",
                )
            },
        ),
        (
            "Configuration",
            {
                "description": (
                    "Station-level defaults for charge points linked to this station."
                ),
                "fields": (
                    "public_display",
                    "language",
                    "preferred_ocpp_version",
                    "energy_unit",
                    "authorization_policy",
                ),
            },
        ),
        (
            "Visibility",
            {
                "fields": ("owner_users", "owner_groups"),
                "classes": ("collapse",),
            },
        ),
    )
    filter_horizontal = ("owner_users", "owner_groups")
    readonly_fields = ("last_heartbeat",)
    actions = [
        "fetch_station_configuration",
        "toggle_station_rfid_authentication",
        "send_station_rfid_list_to_evcs",
        "update_station_rfids_from_evcs",
        "clear_station_authorization_cache",
        "clear_station_charging_profiles",
    ]

    def get_queryset(self, request):
        """Annotate station settings from related location and model."""

        queryset = super().get_queryset(request)
        return queryset.select_related("location", "station_model")

    def save_model(self, request, obj, form, change):
        """Persist station updates and sync station-managed fields to linked CPs."""

        super().save_model(request, obj, form, change)

        station_fields = {
            "public_display": form.cleaned_data.get("public_display", True),
            "language": form.cleaned_data.get("language"),
            "preferred_ocpp_version": form.cleaned_data.get("preferred_ocpp_version", ""),
            "energy_unit": form.cleaned_data.get("energy_unit", Charger.EnergyUnit.KW),
            "authorization_policy": form.cleaned_data.get("authorization_policy", ""),
            "require_rfid": (
                form.cleaned_data.get("authorization_policy", "")
                != Charger.AuthorizationPolicy.OPEN
            ),
            "display_name": obj.display_name,
            "location": obj.location,
            "station_model": obj.station_model,
        }
        Charger.objects.filter(charging_station=obj).update(**station_fields)

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

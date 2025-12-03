from django import forms
from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin

from .models import GoogleMapsLocation, Location


class LocationAdminForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = "__all__"
        widgets = {
            "latitude": forms.NumberInput(attrs={"step": "any"}),
            "longitude": forms.NumberInput(attrs={"step": "any"}),
        }

    class Media:
        css = {"all": ("https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",)}
        js = (
            "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
            "ocpp/charger_map.js",
        )


@admin.register(Location)
class LocationAdmin(EntityModelAdmin):
    form = LocationAdminForm
    list_display = (
        "name",
        "zone",
        "contract_type",
        "city",
        "state",
        "is_public",
        "assigned_to",
    )
    list_filter = ("zone", "contract_type", "city", "state", "country", "is_public")
    search_fields = ("name", "city", "state", "postal_code", "country")
    autocomplete_fields = ("assigned_to",)
    change_form_template = "admin/ocpp/location/change_form.html"


@admin.register(GoogleMapsLocation)
class GoogleMapsLocationAdmin(EntityModelAdmin):
    list_display = ("location", "place_id", "formatted_address")
    search_fields = (
        "location__name",
        "place_id",
        "formatted_address",
    )
    autocomplete_fields = ("location",)

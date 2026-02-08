from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.maps.models import Location


class ChargerLocationSetupForm(forms.Form):
    location = forms.ModelChoiceField(
        queryset=Location.objects.order_by("name"),
        required=False,
        label=_("Existing location"),
    )
    location_name = forms.CharField(
        max_length=200,
        required=False,
        label=_("Location name"),
        help_text=_(
            "Provide a name for a new location or update the existing name."
        ),
    )
    latitude = forms.DecimalField(
        max_digits=9,
        decimal_places=6,
        required=False,
        label=_("Latitude"),
        widget=forms.NumberInput(attrs={"step": "any"}),
    )
    longitude = forms.DecimalField(
        max_digits=9,
        decimal_places=6,
        required=False,
        label=_("Longitude"),
        widget=forms.NumberInput(attrs={"step": "any"}),
    )

    class Media:
        css = {"all": ("https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",)}
        js = (
            "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
            "ocpp/charger_map.js",
        )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is None:
            return
        if getattr(user, "is_superuser", False):
            return
        location_field = self.fields.get("location")
        if not location_field:
            return
        if not hasattr(Location, "assigned_to"):
            return
        if getattr(user, "is_authenticated", False):
            location_field.queryset = location_field.queryset.filter(
                Q(assigned_to__isnull=True) | Q(assigned_to=user)
            )
        else:
            location_field.queryset = location_field.queryset.filter(
                assigned_to__isnull=True
            )

    def clean(self):
        cleaned = super().clean()
        location = cleaned.get("location")
        name = (cleaned.get("location_name") or "").strip()
        latitude = cleaned.get("latitude")
        longitude = cleaned.get("longitude")

        if not location and not name:
            self.add_error(
                "location_name",
                _("Select a location or provide a new location name."),
            )

        if (latitude is None) ^ (longitude is None):
            self.add_error(
                "latitude",
                _("Provide both latitude and longitude to update coordinates."),
            )
            self.add_error(
                "longitude",
                _("Provide both latitude and longitude to update coordinates."),
            )

        cleaned["location_name"] = name
        return cleaned

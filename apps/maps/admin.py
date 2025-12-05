from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
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
    changelist_actions = ["add_current_location"]
    list_display = (
        "name",
        "zone",
        "contract_type",
        "city",
        "state",
        "assigned_to",
    )
    list_filter = ("zone", "contract_type", "city", "state", "country")
    search_fields = ("name", "city", "state", "postal_code", "country")
    autocomplete_fields = ("assigned_to",)
    change_form_template = "admin/ocpp/location/change_form.html"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "add/current/",
                self.admin_site.admin_view(self.add_current_location),
                name="maps_location_add_current_location",
            )
        ]
        return custom + urls

    def get_changelist_actions(self, request):
        parent = getattr(super(), "get_changelist_actions", None)
        actions = []
        if callable(parent):
            parent_actions = parent(request)
            if parent_actions:
                actions.extend(parent_actions)
        if "add_current_location" not in actions:
            actions.append("add_current_location")
        return actions

    def add_current_location(self, request, queryset=None):
        if not self.has_add_permission(request):
            raise PermissionDenied

        title = _("Add Current Location")
        opts = self.model._meta

        if request.method == "POST":
            latitude_value = request.POST.get("latitude")
            longitude_value = request.POST.get("longitude")
            name_value = request.POST.get("name") or _(
                "Current Location %(timestamp)s"
            ) % {"timestamp": timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")}

            try:
                latitude = Decimal(latitude_value)
                longitude = Decimal(longitude_value)
            except (TypeError, InvalidOperation):
                messages.error(
                    request,
                    _(
                        "Unable to read your current location from the browser. Please allow location access and try again."
                    ),
                )
            else:
                extra_fields = {}
                if hasattr(self.model, "assigned_to"):
                    extra_fields["assigned_to"] = (
                        request.user if request.user.is_authenticated else None
                    )

                location = self.model.objects.create(
                    name=name_value,
                    latitude=latitude,
                    longitude=longitude,
                    **extra_fields,
                )

                change_url = reverse(
                    f"admin:{opts.app_label}_{opts.model_name}_change", args=[location.pk]
                )
                return HttpResponseRedirect(change_url)

        context = {
            **self.admin_site.each_context(request),
            "title": title,
            "opts": opts,
        }
        return TemplateResponse(request, "admin/maps/location/add_current.html", context)

    add_current_location.label = _("Add Current Location")
    add_current_location.short_description = _("Add Current Location")
    add_current_location.requires_queryset = False


@admin.register(GoogleMapsLocation)
class GoogleMapsLocationAdmin(EntityModelAdmin):
    list_display = ("location", "place_id", "formatted_address")
    search_fields = (
        "location__name",
        "place_id",
        "formatted_address",
    )
    autocomplete_fields = ("location",)

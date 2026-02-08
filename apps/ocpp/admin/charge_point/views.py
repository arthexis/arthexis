import string

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from apps.maps.models import Location

from ...models import Charger
from .forms import ChargerLocationSetupForm


class ChargerAdminViewsMixin:
    @admin.action(description=_("View in Site"))
    def view_charge_point_dashboard(self, request, queryset=None):
        return HttpResponseRedirect(reverse("ocpp:ocpp-dashboard"))

    @admin.action(description=_("Setup charger location"))
    def setup_charger_location(self, request, queryset):
        charger_ids = list(queryset.values_list("pk", flat=True))
        if not charger_ids:
            self.message_user(
                request,
                _("Select at least one charge point to configure a location."),
                level=messages.WARNING,
            )
            return None
        ids_param = ",".join(str(pk) for pk in charger_ids)
        url = reverse("admin:ocpp_charger_setup_location")
        return HttpResponseRedirect(f"{url}?ids={ids_param}")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "setup-location/",
                self.admin_site.admin_view(self.setup_location_view),
                name="ocpp_charger_setup_location",
            ),
            path(
                "view-in-site/",
                self.admin_site.admin_view(self.view_charge_point_dashboard),
                name="ocpp_charger_view_charge_point_dashboard",
            ),
        ]
        return custom + urls

    def _location_suffix(self, index: int) -> str:
        if index <= 0:
            return ""
        letters = string.ascii_uppercase
        result = ""
        while index > 0:
            index, remainder = divmod(index - 1, len(letters))
            result = letters[remainder] + result
        return result

    def _apply_location_names(self, chargers: list[Charger], location_name: str) -> None:
        grouped: dict[str, list[Charger]] = {}
        for charger in chargers:
            grouped.setdefault(charger.charger_id, []).append(charger)

        chargers_to_update = []
        for group in grouped.values():
            main = [c for c in group if c.connector_id is None]
            connectors = sorted(
                (c for c in group if c.connector_id is not None),
                key=lambda c: c.connector_id or 0,
            )
            for charger in main:
                charger.display_name = location_name
                chargers_to_update.append(charger)
            for index, charger in enumerate(connectors, start=1):
                suffix = self._location_suffix(index)
                charger.display_name = f"{location_name} {suffix}".strip()
                chargers_to_update.append(charger)

        if chargers_to_update:
            Charger.objects.bulk_update(chargers_to_update, ["display_name"])

    def setup_location_view(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied

        raw_ids = (
            request.POST.get("ids")
            if request.method == "POST"
            else request.GET.get("ids")
        )
        ids = (
            [int(value) for value in raw_ids.split(",") if value.isdigit()]
            if raw_ids
            else []
        )
        if not ids:
            self.message_user(
                request,
                _("No chargers selected."),
                level=messages.WARNING,
            )
            return HttpResponseRedirect(reverse("admin:ocpp_charger_changelist"))

        selected = list(
            Charger.visible_for_user(request.user)
            .filter(pk__in=ids)
            .select_related("location")
        )
        if not selected:
            self.message_user(
                request,
                _("Selected chargers were not found."),
                level=messages.ERROR,
            )
            return HttpResponseRedirect(reverse("admin:ocpp_charger_changelist"))

        charger_ids = {charger.charger_id for charger in selected}
        chargers = list(
            Charger.visible_for_user(request.user)
            .filter(charger_id__in=charger_ids)
            .select_related("location")
        )

        initial = {}
        existing_locations = {
            charger.location_id for charger in chargers if charger.location_id
        }
        if len(existing_locations) == 1:
            location_obj = next(
                (charger.location for charger in chargers if charger.location),
                None,
            )
            if location_obj:
                initial["location"] = location_obj
                initial["location_name"] = location_obj.name
                initial["latitude"] = location_obj.latitude
                initial["longitude"] = location_obj.longitude

        if request.method == "POST":
            form = ChargerLocationSetupForm(request.POST, user=request.user)
            if form.is_valid():
                location = form.cleaned_data["location"]
                location_name = form.cleaned_data["location_name"]
                latitude = form.cleaned_data["latitude"]
                longitude = form.cleaned_data["longitude"]

                with transaction.atomic():
                    if location is None:
                        extra_fields = {}
                        if hasattr(Location, "assigned_to"):
                            extra_fields["assigned_to"] = (
                                request.user if request.user.is_authenticated else None
                            )
                        location = Location.objects.create(
                            name=location_name,
                            latitude=latitude,
                            longitude=longitude,
                            **extra_fields,
                        )
                    else:
                        if location_name and location.name != location_name:
                            location.name = location_name
                        if latitude is not None and longitude is not None:
                            location.latitude = latitude
                            location.longitude = longitude
                        location.save()

                    charger_pks = [charger.pk for charger in chargers]
                    if charger_pks:
                        Charger.objects.filter(pk__in=charger_pks).update(location=location)

                    self._apply_location_names(chargers, location.name)

                self.message_user(
                    request,
                    _(
                        "Updated %(count)d chargers with location %(location)s."
                    )
                    % {"count": len(chargers), "location": location.name},
                    level=messages.SUCCESS,
                )
                return HttpResponseRedirect(reverse("admin:ocpp_charger_changelist"))
        else:
            form = ChargerLocationSetupForm(initial=initial, user=request.user)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Setup charger location"),
            "chargers": chargers,
            "form": form,
            "ids": ",".join(str(pk) for pk in ids),
        }
        return TemplateResponse(
            request,
            "admin/ocpp/charger/setup_location.html",
            context,
        )

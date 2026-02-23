"""Simulator creation charger admin actions."""

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _, ngettext

from ....models import Charger, ChargerConfiguration, Simulator


class SimulatorActionsMixin:
    """Actions and helpers for simulator creation from chargers."""

    def _simulator_base_name(self, charger: Charger) -> str:
        display_name = self._charger_display_name(charger)
        connector_suffix = f" {charger.connector_label}" if charger.connector_id is not None else ""
        return (f"{display_name}{connector_suffix} Simulator".strip()) or "Charge Point Simulator"

    def _trim_with_suffix(self, base: str, suffix: str, *, max_length: int) -> str:
        """Trim base so the suffix fits in max length and append suffix."""
        base = base[: max_length - len(suffix)] if len(base) + len(suffix) > max_length else base
        return f"{base}{suffix}"

    def _unique_with_suffix(self, base: str, *, default: str, max_length: int, exists, suffix_format: str) -> str:
        """Generate a unique value constrained by field length and suffix format."""
        base = (base or default).strip()[: max(max_length, 1)]
        candidate, counter = base or default, 2
        while exists(candidate):
            candidate = self._trim_with_suffix(base or default, suffix_format.format(counter=counter), max_length=max_length)
            counter += 1
        return candidate

    def _unique_simulator_name(self, base: str) -> str:
        return self._unique_with_suffix(base, default="Simulator", max_length=Simulator._meta.get_field("name").max_length, exists=lambda candidate: Simulator.objects.filter(name=candidate).exists(), suffix_format=" ({counter})")

    def _simulator_cp_path_base(self, charger: Charger) -> str:
        path = (charger.last_path or "").strip().strip("/") or charger.charger_id.strip().strip("/")
        connector_slug = charger.connector_slug
        if connector_slug and connector_slug != Charger.AGGREGATE_CONNECTOR_SLUG:
            path = f"{path}-{connector_slug}" if path else connector_slug
        return path or "SIMULATOR"

    def _unique_simulator_cp_path(self, base: str) -> str:
        return self._unique_with_suffix((base or "SIMULATOR").strip().strip("/"), default="SIMULATOR", max_length=Simulator._meta.get_field("cp_path").max_length, exists=lambda candidate: Simulator.objects.filter(cp_path__iexact=candidate).exists(), suffix_format="-sim{counter}")

    def _simulator_configuration(self, charger: Charger) -> ChargerConfiguration | None:
        return charger.configuration if charger.configuration_id else None

    def _create_simulator_from_charger(self, charger: Charger) -> Simulator:
        return Simulator.objects.create(name=self._unique_simulator_name(self._simulator_base_name(charger)), cp_path=self._unique_simulator_cp_path(self._simulator_cp_path_base(charger)), serial_number=charger.charger_id, connector_id=charger.connector_id if charger.connector_id is not None else 1, configuration=self._simulator_configuration(charger))

    def _report_simulator_error(self, request, charger: Charger, error: Exception) -> None:
        if isinstance(error, ValidationError):
            messages_list = []
            if getattr(error, "message_dict", None):
                for field_errors in error.message_dict.values():
                    messages_list.extend(str(item) for item in field_errors)
            elif getattr(error, "messages", None):
                messages_list.extend(str(item) for item in error.messages)
            else:
                messages_list.append(str(error))
        else:
            messages_list = [str(error)]
        for message_text in messages_list:
            self.message_user(request, _("Unable to create simulator for %(charger)s: %(error)s") % {"charger": self._charger_display_name(charger), "error": message_text}, level=messages.ERROR)

    @admin.action(description=_("Create Simulator for CPs"))
    def create_simulator_for_cp(self, request, queryset):
        created: list[tuple[Charger, Simulator]] = []
        for charger in queryset:
            try:
                simulator = self._create_simulator_from_charger(charger)
            except Exception as exc:  # pragma: no cover - defensive
                self._report_simulator_error(request, charger, exc)
            else:
                created.append((charger, simulator))
        if not created:
            self.message_user(request, _("No simulators were created."), level=messages.WARNING)
            return None
        first_charger, first_simulator = created[0]
        change_url = reverse("admin:ocpp_simulator_change", args=[first_simulator.pk])
        link = format_html('<a href="{}">{}</a>', change_url, first_simulator.name)
        total = len(created)
        self.message_user(request, format_html(ngettext("Created {count} simulator for the selected charge point. First simulator: {simulator}.", "Created {count} simulators for the selected charge points. First simulator: {simulator}.", total), count=total, simulator=link), level=messages.SUCCESS)
        if total == 1:
            self.message_user(request, format_html(_("Configured for {charger_name}."), charger_name=self._charger_display_name(first_charger)))
        return HttpResponseRedirect(change_url)

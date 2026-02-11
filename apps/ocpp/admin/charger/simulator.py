"""Simulator-related helpers and actions."""

from .utils import charger_display_name
from ..common_imports import *


class ChargerSimulatorMixin:
    """Mixin for creating simulators from charge points."""

    def _simulator_base_name(self, charger: Charger) -> str:
        display_name = charger_display_name(charger)
        connector_suffix = ""
        if charger.connector_id is not None:
            connector_suffix = f" {charger.connector_label}"
        base = f"{display_name}{connector_suffix} Simulator".strip()
        return base or "Charge Point Simulator"

    def _trim_with_suffix(self, base: str, suffix: str, *, max_length: int) -> str:
        base = base[: max_length - len(suffix)] if len(base) + len(suffix) > max_length else base
        return f"{base}{suffix}"

    def _unique_simulator_name(self, base: str) -> str:
        base = (base or "Simulator").strip()
        max_length = Simulator._meta.get_field("name").max_length
        base = base[:max_length]
        candidate = base or "Simulator"
        counter = 2
        while Simulator.objects.filter(name=candidate).exists():
            suffix = f" ({counter})"
            candidate = self._trim_with_suffix(base or "Simulator", suffix, max_length=max_length)
            counter += 1
        return candidate

    def _simulator_cp_path_base(self, charger: Charger) -> str:
        path = (charger.last_path or "").strip().strip("/")
        if not path:
            path = charger.charger_id.strip().strip("/")
        connector_slug = charger.connector_slug
        if connector_slug and connector_slug != Charger.AGGREGATE_CONNECTOR_SLUG:
            path = f"{path}-{connector_slug}" if path else connector_slug
        return path or "SIMULATOR"

    def _unique_simulator_cp_path(self, base: str) -> str:
        base = (base or "SIMULATOR").strip().strip("/")
        max_length = Simulator._meta.get_field("cp_path").max_length
        base = base[:max_length]
        candidate = base or "SIMULATOR"
        counter = 2
        while Simulator.objects.filter(cp_path__iexact=candidate).exists():
            suffix = f"-sim{counter}"
            candidate = self._trim_with_suffix(base or "SIMULATOR", suffix, max_length=max_length)
            counter += 1
        return candidate

    def _simulator_configuration(self, charger: Charger) -> ChargerConfiguration | None:
        if charger.configuration_id:
            return charger.configuration
        return None

    def _create_simulator_from_charger(self, charger: Charger) -> Simulator:
        name = self._unique_simulator_name(self._simulator_base_name(charger))
        cp_path_base = self._simulator_cp_path_base(charger)
        cp_path = self._unique_simulator_cp_path(cp_path_base)
        connector_id = charger.connector_id if charger.connector_id is not None else 1
        simulator = Simulator.objects.create(
            name=name,
            cp_path=cp_path,
            serial_number=charger.charger_id,
            connector_id=connector_id,
            configuration=self._simulator_configuration(charger),
        )
        return simulator

    def _report_simulator_error(self, request, charger: Charger, error: Exception) -> None:
        if isinstance(error, ValidationError):
            messages_list: list[str] = []
            if getattr(error, "message_dict", None):
                for field_errors in error.message_dict.values():
                    messages_list.extend(str(item) for item in field_errors)
            elif getattr(error, "messages", None):
                messages_list.extend(str(item) for item in error.messages)
            else:
                messages_list.append(str(error))
        else:
            messages_list = [str(error)]

        charger_name = charger_display_name(charger)
        for message_text in messages_list:
            self.message_user(
                request,
                _("Unable to create simulator for %(charger)s: %(error)s")
                % {"charger": charger_name, "error": message_text},
                level=messages.ERROR,
            )

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
            self.message_user(
                request,
                _("No simulators were created."),
                level=messages.WARNING,
            )
            return None

        first_charger, first_simulator = created[0]
        first_label = charger_display_name(first_charger)
        change_url = reverse("admin:ocpp_simulator_change", args=[first_simulator.pk])
        link = format_html('<a href="{}">{}</a>', change_url, first_simulator.name)
        total = len(created)
        message = format_html(
            ngettext(
                "Created {count} simulator for the selected charge point. First simulator: {simulator}.",
                "Created {count} simulators for the selected charge points. First simulator: {simulator}.",
                total,
            ),
            count=total,
            simulator=link,
        )
        self.message_user(request, message, level=messages.SUCCESS)
        if total == 1:
            detail_message = format_html(
                _("Configured for {charger_name}."),
                charger_name=first_label,
            )
            self.message_user(request, detail_message)
        return HttpResponseRedirect(change_url)

from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.contrib.admin.sites import NotRegistered
from django import forms

import asyncio
import base64
import contextlib
import json
import time as time_module
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests
from asgiref.sync import async_to_sync
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings
from django.contrib.admin.utils import quote
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.db.models.deletion import ProtectedError
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import formats, timezone, translation
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html, format_html_join
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _, ngettext
from requests import RequestException

from apps.cards.models import RFID as CoreRFID
from apps.core.admin import SaveBeforeChangeAction
from apps.core.form_fields import SchedulePeriodsField
from apps.locals.user_data import EntityModelAdmin
from apps.nodes.models import Node
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel
from apps.energy.models import EnergyTariff

from .. import store
from ..models import (
    CPFirmware,
    CPFirmwareDeployment,
    CPFirmwareRequest,
    CPForwarder,
    CPNetworkProfile,
    CPNetworkProfileDeployment,
    CPReservation,
    Charger,
    ChargerConfiguration,
    ConfigurationKey,
    ChargerLogRequest,
    ChargingProfile,
    ChargingProfileDispatch,
    ChargingSchedule,
    DataTransferMessage,
    CustomerInformationRequest,
    CustomerInformationChunk,
    DisplayMessageNotification,
    DisplayMessage,
    MeterValue,
    PowerProjection,
    RFIDSessionAttempt,
    SecurityEvent,
    Simulator,
    StationModel,
    Transaction,
    CertificateRequest,
    CertificateStatusCheck,
    CertificateOperation,
    InstalledCertificate,
    TrustAnchor,
)
from ..simulator import ChargePointSimulator
from ..status_display import ERROR_OK_VALUES, STATUS_BADGE_MAP
from ..status_resets import clear_stale_cached_statuses
from ..transactions_io import (
    export_transactions,
    import_transactions as import_transactions_data,
)
from ..views import _charger_state, _live_sessions


# Ensure admin reloads (e.g., in tests) do not fail due to existing registrations.
for _model in (
    ChargerConfiguration,
    ConfigurationKey,
    DataTransferMessage,
    CPFirmware,
    CPFirmwareDeployment,
    ChargingProfile,
    CPReservation,
    PowerProjection,
    Charger,
    Simulator,
    Transaction,
    MeterValue,
    SecurityEvent,
    ChargerLogRequest,
    CPForwarder,
    StationModel,
    CPNetworkProfile,
    CPNetworkProfileDeployment,
    CPFirmwareRequest,
    RFIDSessionAttempt,
    CertificateRequest,
    CertificateStatusCheck,
    CertificateOperation,
    InstalledCertificate,
    TrustAnchor,
    CustomerInformationRequest,
    CustomerInformationChunk,
    DisplayMessageNotification,
    DisplayMessage,
):
    try:
        admin.site.unregister(_model)
    except NotRegistered:
        pass


class TransactionExportForm(forms.Form):
    start = forms.DateTimeField(required=False)
    end = forms.DateTimeField(required=False)
    chargers = forms.ModelMultipleChoiceField(
        queryset=Charger.objects.all(), required=False
    )


class TransactionImportForm(forms.Form):
    file = forms.FileField()


class ChargingProfileSendForm(forms.Form):
    charger = forms.ModelChoiceField(
        queryset=Charger.objects.all(),
        label=_("EVCS"),
        help_text=_("Charger that will receive the bundled profile."),
    )


class ChargingScheduleForm(forms.ModelForm):
    charging_schedule_periods = SchedulePeriodsField(
        label=_("Schedule periods"),
        help_text=_("Define the periods that make up the charging schedule."),
    )

    class Meta:
        model = ChargingSchedule
        fields = "__all__"


class CPReservationForm(forms.ModelForm):
    class Meta:
        model = CPReservation
        fields = [
            "location",
            "account",
            "rfid",
            "id_tag",
            "start_time",
            "duration_minutes",
        ]

    def clean(self):
        cleaned = super().clean()
        instance = self.instance
        for field in self.Meta.fields:
            if field in cleaned:
                setattr(instance, field, cleaned[field])
        try:
            instance.allocate_connector(force=bool(instance.pk))
        except ValidationError as exc:
            if exc.message_dict:
                for field, errors in exc.message_dict.items():
                    for error in errors:
                        self.add_error(field, error)
                raise forms.ValidationError(
                    _("Unable to allocate a connector for the selected time window.")
                )
            raise forms.ValidationError(exc.messages or [str(exc)])
        if not instance.id_tag_value:
            message = _("Select an RFID or provide an idTag for the reservation.")
            self.add_error("id_tag", message)
            self.add_error("rfid", message)
            raise forms.ValidationError(message)
        return cleaned


class ConfigurationKeyInlineForm(forms.ModelForm):
    value_input = forms.CharField(
        label=_("Value"),
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 1,
                "class": "vTextField config-value-input",
                "spellcheck": "false",
                "autocomplete": "off",
            }
        ),
    )

    class Meta:
        model = ConfigurationKey
        fields: list[str] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["value_input"]
        field.widget.attrs["data-config-key"] = self.instance.key
        if self.instance.has_value:
            field.initial = self._format_initial_value(self.instance.value)
        else:
            field.disabled = True
            field.widget.attrs["placeholder"] = "-"
            field.widget.attrs["aria-disabled"] = "true"
        self.extra_display = self._format_extra_data()

    @staticmethod
    def _format_initial_value(value: object) -> str:
        if value in (None, ""):
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, indent=2, ensure_ascii=False)
        return str(value)

    def clean_value_input(self) -> str:
        raw_value = self.cleaned_data.get("value_input", "")
        if not self.instance.has_value:
            self._parsed_value = self.instance.value
            self._has_value = False
            return ""
        text = raw_value.strip()
        if not text:
            self._parsed_value = None
            self._has_value = False
            return ""
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = raw_value
        self._parsed_value = parsed
        self._has_value = True
        return raw_value

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.instance.has_value:
            has_value = getattr(self, "_has_value", self.instance.has_value)
            parsed = getattr(self, "_parsed_value", instance.value)
            instance.has_value = has_value
            instance.value = parsed if has_value else None
        if commit:
            instance.save()
        return instance

    def _format_extra_data(self) -> str:
        if not self.instance.extra_data:
            return ""
        formatted = json.dumps(
            self.instance.extra_data, indent=2, ensure_ascii=False
        )
        return format_html("<pre>{}</pre>", formatted)


class PushConfigurationForm(forms.Form):
    chargers = forms.ModelMultipleChoiceField(
        label=_("Charge points"),
        required=True,
        queryset=Charger.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Only EVCS entries are eligible for configuration updates."),
    )

    def __init__(self, *args, chargers_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = chargers_queryset or Charger.objects.none()
        self.fields["chargers"].queryset = queryset


class UploadFirmwareForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        default_date = timezone.now() + timedelta(minutes=1)
        if timezone.is_naive(default_date):
            default_date = timezone.make_aware(
                default_date, timezone.get_current_timezone()
            )
        self.fields["retrieve_date"].initial = timezone.localtime(default_date)
        self.fields["chargers"].queryset = (
            Charger.objects.filter(connector_id__isnull=True)
            .order_by("display_name", "charger_id")
        )

    chargers = forms.ModelMultipleChoiceField(
        label=_("Charge points"),
        queryset=Charger.objects.none(),
        help_text=_("Select the EVCS units to update."),
    )
    retrieve_date = forms.DateTimeField(
        label=_("Retrieve date"),
        required=False,
        help_text=_("When the EVCS should start downloading the firmware."),
    )
    retries = forms.IntegerField(
        label=_("Retries"),
        required=False,
        min_value=0,
        initial=1,
        help_text=_("Number of download attempts before giving up."),
    )
    retry_interval = forms.IntegerField(
        label=_("Retry interval (seconds)"),
        required=False,
        min_value=0,
        initial=600,
        help_text=_("Seconds between retry attempts."),
    )

    def clean_retrieve_date(self):
        value = self.cleaned_data.get("retrieve_date")
        if value is None:
            return None
        if timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_current_timezone())
        return value

    def clean(self):
        cleaned = super().clean()
        chargers = cleaned.get("chargers")
        if not chargers:
            self.add_error(
                "chargers",
                _("Select at least one charge point to receive the firmware."),
            )
        return cleaned


class SetNetworkProfileForm(forms.Form):
    chargers = forms.ModelMultipleChoiceField(
        label=_("Charge points"),
        queryset=Charger.objects.none(),
        help_text=_("Select EVCS units that should receive this network profile."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["chargers"].queryset = (
            Charger.objects.filter(connector_id__isnull=True)
            .order_by("display_name", "charger_id")
            .all()
        )

    def clean(self):
        cleaned = super().clean()
        chargers = cleaned.get("chargers")
        if not chargers:
            self.add_error("chargers", _("Select at least one charge point."))
        return cleaned


class LogViewAdminMixin:
    """Mixin providing an admin view to display charger or simulator logs."""

    log_type = "charger"
    log_template_name = "admin/ocpp/log_view.html"

    def get_log_identifier(self, obj):  # pragma: no cover - mixin hook
        raise NotImplementedError

    def get_log_title(self, obj):
        return f"Log for {obj}"

    def get_urls(self):
        urls = super().get_urls()
        info = self.model._meta.app_label, self.model._meta.model_name
        custom = [
            path(
                "<path:object_id>/log/",
                self.admin_site.admin_view(self.log_view),
                name=f"{info[0]}_{info[1]}_log",
            ),
        ]
        return custom + urls

    def log_view(self, request, object_id):
        obj = self.get_object(request, object_id)
        if obj is None:
            info = self.model._meta.app_label, self.model._meta.model_name
            changelist_url = reverse(
                "admin:%s_%s_changelist" % info,
                current_app=self.admin_site.name,
            )
            self.message_user(request, "Log is not available.", messages.ERROR)
            return redirect(changelist_url)
        identifier = self.get_log_identifier(obj)
        log_entries = store.get_logs(identifier, log_type=self.log_type)
        log_file = store._file_path(identifier, log_type=self.log_type)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": obj,
            "title": self.get_log_title(obj),
            "log_entries": log_entries,
            "log_file": str(log_file),
            "log_identifier": identifier,
        }
        return TemplateResponse(request, self.log_template_name, context)


class ConfigurationKeyInline(admin.TabularInline):
    model = ConfigurationKey
    extra = 0
    can_delete = False
    ordering = ("position", "id")
    form = ConfigurationKeyInlineForm
    template = "admin/ocpp/chargerconfiguration/configuration_inline.html"
    readonly_fields = ("position", "key", "readonly", "extra_display")
    fields = ("position", "key", "readonly", "value_input", "extra_display")
    show_change_link = False

    def has_add_permission(self, request, obj=None):  # pragma: no cover - admin hook
        return False

    @admin.display(description=_("Value"))
    def value_display(self, obj):
        if not obj.has_value:
            return "-"
        value = obj.value
        if isinstance(value, (dict, list)):
            formatted = json.dumps(value, indent=2, ensure_ascii=False)
            return format_html("<pre>{}</pre>", formatted)
        if value in (None, ""):
            return "-"
        return str(value)

    @admin.display(description=_("Extra data"))
    def extra_display(self, obj):
        if not obj.extra_data:
            return "-"
        formatted = json.dumps(obj.extra_data, indent=2, ensure_ascii=False)
        return format_html("<pre>{}</pre>", formatted)


@admin.register(ChargerConfiguration)
class ChargerConfigurationAdmin(admin.ModelAdmin):
    change_form_template = "admin/ocpp/chargerconfiguration/change_form.html"
    list_display = (
        "charger_identifier",
        "connector_display",
        "origin_display",
        "created_at",
    )
    list_filter = ("connector_id",)
    search_fields = ("charger_identifier",)
    actions = ("refetch_cp_configurations",)
    readonly_fields = (
        "charger_identifier",
        "connector_id",
        "origin_display",
        "evcs_snapshot_at",
        "created_at",
        "updated_at",
        "linked_chargers",
        "unknown_keys_display",
        "raw_payload_download_link",
    )
    inlines = (ConfigurationKeyInline,)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "charger_identifier",
                    "connector_id",
                    "origin_display",
                    "evcs_snapshot_at",
                    "linked_chargers",
                    "created_at",
                    "updated_at",
                )
            },
        ),
        (
            "Payload",
            {
                "fields": (
                    "unknown_keys_display",
                    "raw_payload_download_link",
                )
            },
        ),
    )

    @admin.display(description="Connector")
    def connector_display(self, obj):
        if obj.connector_id is None:
            return "All"
        return obj.connector_id

    @admin.display(description="Linked charge points")
    def linked_chargers(self, obj):
        if obj.pk is None:
            return ""
        linked = [charger.identity_slug() for charger in obj.chargers.all()]
        if not linked:
            return "-"
        return ", ".join(sorted(linked))

    def _render_json(self, data):
        from django.utils.html import format_html

        if not data:
            return "-"
        formatted = json.dumps(data, indent=2, ensure_ascii=False)
        return format_html("<pre>{}</pre>", formatted)

    @admin.display(description="unknownKey")
    def unknown_keys_display(self, obj):
        return self._render_json(obj.unknown_keys)

    @admin.display(description="Raw payload")
    def raw_payload_download_link(self, obj):
        if obj.pk is None:
            return ""
        if not obj.raw_payload:
            return "-"
        download_url = reverse(
            "admin:ocpp_chargerconfiguration_download_raw",
            args=[quote(obj.pk)],
        )
        return format_html(
            '<a href="{}" class="button">{}</a>',
            download_url,
            _("Download raw JSON"),
        )

    def _available_push_chargers(self):
        queryset = Charger.objects.filter(connector_id__isnull=True)
        local = Node.get_local()
        if local:
            queryset = queryset.filter(
                Q(node_origin__isnull=True) | Q(node_origin=local)
            )
        else:
            queryset = queryset.filter(node_origin__isnull=True)
        return queryset.order_by("display_name", "charger_id")

    def _serialize_configuration_value(self, value: object) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if value in (None, ""):
            return ""
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    def _send_change_configuration_call(
        self,
        charger: Charger,
        key: str,
        value_text: str,
    ) -> tuple[bool, str | None, str]:
        connector_value = charger.connector_id
        ws = store.get_connection(charger.charger_id, connector_value)
        if ws is None:
            message = _("%(charger)s is not connected to the platform.") % {
                "charger": charger,
            }
            return False, None, message

        payload = {"key": key}
        if value_text is not None:
            payload["value"] = value_text
        message_id = uuid.uuid4().hex
        frame = json.dumps([2, message_id, "ChangeConfiguration", payload])
        try:
            async_to_sync(ws.send)(frame)
        except Exception as exc:  # pragma: no cover - network failure
            message = _("Failed to send ChangeConfiguration: %(error)s") % {
                "error": exc,
            }
            return False, None, message

        log_key = store.identity_key(charger.charger_id, connector_value)
        store.add_log(log_key, f"< {frame}", log_type="charger")
        store.add_log(
            log_key,
            _("Requested configuration change for %(key)s.") % {"key": key},
            log_type="charger",
        )
        metadata = {
            "action": "ChangeConfiguration",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "key": key,
            "log_key": log_key,
            "requested_at": timezone.now(),
        }
        store.register_pending_call(message_id, metadata)
        store.schedule_call_timeout(
            message_id,
            timeout=10.0,
            action="ChangeConfiguration",
            log_key=log_key,
            message=_("ChangeConfiguration timed out: charger did not respond"),
        )

        result = store.wait_for_pending_call(message_id, timeout=10.0)
        if result is None:
            message = _(
                "ChangeConfiguration did not receive a response from the charger."
            )
            return False, None, message

        if not result.get("success", True):
            description = str(result.get("error_description") or "").strip()
            details = result.get("error_details")
            if details and not description:
                try:
                    description = json.dumps(details, ensure_ascii=False)
                except TypeError:
                    description = str(details)
            if not description:
                description = _("Unknown error")
            message = _(
                "ChangeConfiguration failed: %(details)s"
            ) % {"details": description}
            return False, None, message

        payload_result = result.get("payload")
        status_value = ""
        if isinstance(payload_result, dict):
            status_value = str(payload_result.get("status") or "").strip()
        normalized = status_value.casefold()
        if not status_value:
            message = _("ChangeConfiguration response did not include a status.")
            return False, None, message
        if normalized not in {"accepted", "rebootrequired"}:
            message = _("ChangeConfiguration returned %(status)s.") % {
                "status": status_value,
            }
            return False, status_value, message
        success_message = _("Configuration updated.")
        return True, status_value or "Accepted", success_message

    def _apply_configuration_to_charger(
        self,
        configuration: ChargerConfiguration,
        charger: Charger,
    ) -> tuple[bool, str, bool]:
        if not charger.is_local:
            message = _(
                "Only charge points managed by this node can receive configuration updates."
            )
            return False, message, False

        entries = list(configuration.configuration_entries.order_by("position", "id"))
        editable = [entry for entry in entries if entry.has_value and not entry.readonly]
        if not editable:
            message = _(
                "This configuration does not include editable keys with values."
            )
            return False, message, False

        applied = 0
        needs_restart = False
        for entry in editable:
            value_text = self._serialize_configuration_value(entry.value)
            ok, status, detail = self._send_change_configuration_call(
                charger, entry.key, value_text
            )
            if not ok:
                return False, detail, needs_restart
            applied += 1
            if (status or "").casefold() == "rebootrequired":
                needs_restart = True

        if applied:
            Charger.objects.filter(pk=charger.pk).update(configuration=configuration)

        message = ngettext(
            "Applied %(count)d configuration key.",
            "Applied %(count)d configuration keys.",
            applied,
        ) % {"count": applied}
        if needs_restart:
            message = _("%(message)s Charger restart required.") % {
                "message": message,
            }
        return True, message, needs_restart

    def _restart_charger(self, charger: Charger) -> tuple[bool, str]:
        if not charger.is_local:
            message = _(
                "Only local charge points can be restarted from this server."
            )
            return False, message

        connector_value = charger.connector_id
        ws = store.get_connection(charger.charger_id, connector_value)
        if ws is None:
            message = _("%(charger)s is not connected to the platform.") % {
                "charger": charger,
            }
            return False, message

        message_id = uuid.uuid4().hex
        frame = json.dumps([2, message_id, "Reset", {"type": "Soft"}])
        try:
            async_to_sync(ws.send)(frame)
        except Exception as exc:  # pragma: no cover - network failure
            message = _("Failed to send Reset: %(error)s") % {"error": exc}
            return False, message

        log_key = store.identity_key(charger.charger_id, connector_value)
        store.add_log(log_key, f"< {frame}", log_type="charger")
        metadata = {
            "action": "Reset",
            "charger_id": charger.charger_id,
            "connector_id": connector_value,
            "log_key": log_key,
            "requested_at": timezone.now(),
        }
        store.register_pending_call(message_id, metadata)
        store.schedule_call_timeout(
            message_id,
            timeout=10.0,
            action="Reset",
            log_key=log_key,
            message=_("Reset timed out: charger did not respond"),
        )

        result = store.wait_for_pending_call(message_id, timeout=10.0)
        if result is None:
            return False, _(
                "Reset did not receive a response from the charger."
            )
        if not result.get("success", True):
            description = str(result.get("error_description") or "").strip()
            if not description:
                description = _("Unknown error")
            return False, _("Reset failed: %(details)s") % {"details": description}

        payload_result = result.get("payload")
        status_value = ""
        if isinstance(payload_result, dict):
            status_value = str(payload_result.get("status") or "").strip()
        if status_value.casefold() != "accepted":
            return False, _("Reset returned %(status)s.") % {"status": status_value}

        deadline = time_module.monotonic() + 60.0
        time_module.sleep(2.0)
        while time_module.monotonic() < deadline:
            if store.is_connected(charger.charger_id, connector_value):
                return True, _("Charger restarted successfully.")
            time_module.sleep(2.0)
        return False, _(
            "Charger has not reconnected yet. Verify its status from the charger list."
        )

    def push_configuration_view(self, request, object_id, *args, **kwargs):
        configuration = self.get_object(request, object_id)
        if configuration is None:
            raise Http404("Configuration not found")

        available = self._available_push_chargers()
        selected_chargers: list[Charger] = []
        auto_start = False

        if request.method == "POST":
            form = PushConfigurationForm(request.POST, chargers_queryset=available)
            if form.is_valid():
                selected_chargers = list(form.cleaned_data["chargers"])
                auto_start = True
        else:
            initial_chargers = list(
                available.filter(
                    pk__in=configuration.chargers.values_list("pk", flat=True)
                )
            )
            initial_ids = [charger.pk for charger in initial_chargers]
            form = PushConfigurationForm(
                chargers_queryset=available,
                initial={"chargers": initial_ids},
            )
            selected_chargers = initial_chargers

        selected_payload = [
            {
                "id": charger.pk,
                "label": charger.display_name or charger.charger_id,
                "identifier": charger.identity_slug(),
                "serial": charger.charger_id,
            }
            for charger in selected_chargers
        ]

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": configuration,
            "title": _("Push configuration to EVCS"),
            "configuration": configuration,
            "form": form,
            "media": self.media + form.media,
            "selected_chargers": selected_chargers,
            "selected_payload": selected_payload,
            "selected_payload_json": json.dumps(selected_payload, ensure_ascii=False),
            "progress_url": reverse(
                "admin:ocpp_chargerconfiguration_push_progress",
                args=[quote(configuration.pk)],
            ),
            "restart_url": reverse(
                "admin:ocpp_chargerconfiguration_push_restart",
                args=[quote(configuration.pk)],
            ),
            "auto_start": auto_start,
        }
        return TemplateResponse(
            request,
            "admin/ocpp/chargerconfiguration/push_configuration.html",
            context,
        )

    def push_configuration_progress(self, request, object_id, *args, **kwargs):
        if request.method != "POST":
            return JsonResponse({"detail": "POST required"}, status=405)
        configuration = self.get_object(request, object_id)
        if configuration is None:
            return JsonResponse({"detail": "Not found"}, status=404)
        charger_id = request.POST.get("charger")
        if not charger_id:
            return JsonResponse({"detail": "charger required"}, status=400)
        try:
            charger = self._available_push_chargers().get(pk=charger_id)
        except Charger.DoesNotExist:
            return JsonResponse({"detail": "invalid charger"}, status=404)

        success, message, needs_restart = self._apply_configuration_to_charger(
            configuration, charger
        )
        status = 200 if success else 400
        payload = {
            "ok": bool(success),
            "message": message,
            "needs_restart": bool(needs_restart),
        }
        return JsonResponse(payload, status=status)

    def restart_configuration_targets(self, request, object_id, *args, **kwargs):
        if request.method != "POST":
            return JsonResponse({"detail": "POST required"}, status=405)
        configuration = self.get_object(request, object_id)
        if configuration is None:
            return JsonResponse({"detail": "Not found"}, status=404)
        charger_id = request.POST.get("charger")
        if not charger_id:
            return JsonResponse({"detail": "charger required"}, status=400)
        try:
            charger = self._available_push_chargers().get(pk=charger_id)
        except Charger.DoesNotExist:
            return JsonResponse({"detail": "invalid charger"}, status=404)

        success, message = self._restart_charger(charger)
        status = 200 if success else 400
        return JsonResponse({"ok": bool(success), "message": message}, status=status)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/raw-payload/",
                self.admin_site.admin_view(self.download_raw_payload),
                name="ocpp_chargerconfiguration_download_raw",
            ),
            path(
                "<path:object_id>/push/",
                self.admin_site.admin_view(self.push_configuration_view),
                name="ocpp_chargerconfiguration_push",
            ),
            path(
                "<path:object_id>/push/progress/",
                self.admin_site.admin_view(self.push_configuration_progress),
                name="ocpp_chargerconfiguration_push_progress",
            ),
            path(
                "<path:object_id>/push/restart/",
                self.admin_site.admin_view(self.restart_configuration_targets),
                name="ocpp_chargerconfiguration_push_restart",
            ),
        ]
        return custom_urls + urls

    def download_raw_payload(self, request, object_id, *args, **kwargs):
        configuration = self.get_object(request, object_id)
        if configuration is None or not configuration.raw_payload:
            raise Http404("Raw payload not available.")

        payload = json.dumps(configuration.raw_payload, indent=2, ensure_ascii=False)
        filename = f"{slugify(configuration.charger_identifier) or 'cp-configuration'}-payload.json"

        response = HttpResponse(payload, content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @admin.display(description="Origin")
    def origin_display(self, obj):
        if obj.evcs_snapshot_at:
            return "EVCS"
        return "Local"

    def save_model(self, request, obj, form, change):
        obj.evcs_snapshot_at = None
        super().save_model(request, obj, form, change)

    @admin.action(description=_("Re-fetch CP configurations"))
    def refetch_cp_configurations(self, request, queryset):
        charger_admin = self.admin_site._registry.get(Charger)
        if charger_admin is None or not hasattr(
            charger_admin, "fetch_cp_configuration"
        ):
            self.message_user(
                request,
                _("Unable to request configurations: charger admin is unavailable."),
                level=messages.ERROR,
            )
            return

        charger_pks: set[int] = set()
        missing: list[ChargerConfiguration] = []
        for configuration in queryset:
            linked_ids = list(configuration.chargers.values_list("pk", flat=True))
            if not linked_ids:
                fallback = Charger.objects.filter(
                    charger_id=configuration.charger_identifier
                )
                if configuration.connector_id is None:
                    fallback = fallback.filter(connector_id__isnull=True)
                else:
                    fallback = fallback.filter(
                        connector_id=configuration.connector_id
                    )
                linked_ids = list(fallback.values_list("pk", flat=True))
            if not linked_ids:
                missing.append(configuration)
                continue
            charger_pks.update(linked_ids)

        if charger_pks:
            charger_queryset = Charger.objects.filter(pk__in=charger_pks)
            charger_admin.fetch_cp_configuration(request, charger_queryset)

        if missing:
            for configuration in missing:
                self.message_user(
                    request,
                    _(
                        "%(identifier)s has no associated charger to refresh."
                    )
                    % {"identifier": configuration.charger_identifier},
                    level=messages.WARNING,
                )


@admin.register(ConfigurationKey)
class ConfigurationKeyAdmin(admin.ModelAdmin):
    list_display = ("configuration", "key", "position", "readonly")
    ordering = ("configuration", "position", "id")

    def get_model_perms(self, request):  # pragma: no cover - admin hook
        return {}


@admin.register(DataTransferMessage)
class DataTransferMessageAdmin(admin.ModelAdmin):
    list_display = (
        "charger",
        "connector_id",
        "direction",
        "vendor_id",
        "message_id",
        "status",
        "created_at",
        "responded_at",
    )
    list_filter = ("direction", "status")
    search_fields = (
        "charger__charger_id",
        "ocpp_message_id",
        "vendor_id",
        "message_id",
    )
    readonly_fields = (
        "charger",
        "connector_id",
        "direction",
        "ocpp_message_id",
        "vendor_id",
        "message_id",
        "payload",
        "status",
        "response_data",
        "error_code",
        "error_description",
        "error_details",
        "responded_at",
        "created_at",
        "updated_at",
    )


@admin.register(CustomerInformationRequest)
class CustomerInformationRequestAdmin(admin.ModelAdmin):
    list_display = (
        "charger",
        "request_id",
        "ocpp_message_id",
        "last_notified_at",
        "completed_at",
        "created_at",
    )
    search_fields = ("charger__charger_id", "request_id", "ocpp_message_id")
    readonly_fields = (
        "charger",
        "ocpp_message_id",
        "request_id",
        "payload",
        "last_notified_at",
        "completed_at",
        "created_at",
        "updated_at",
    )


@admin.register(CustomerInformationChunk)
class CustomerInformationChunkAdmin(admin.ModelAdmin):
    list_display = (
        "charger",
        "request_id",
        "ocpp_message_id",
        "tbc",
        "received_at",
    )
    list_filter = ("tbc",)
    search_fields = ("charger__charger_id", "request_id", "ocpp_message_id")
    readonly_fields = (
        "charger",
        "request_record",
        "ocpp_message_id",
        "request_id",
        "data",
        "tbc",
        "raw_payload",
        "received_at",
    )


@admin.register(DisplayMessageNotification)
class DisplayMessageNotificationAdmin(admin.ModelAdmin):
    list_display = (
        "charger",
        "request_id",
        "ocpp_message_id",
        "tbc",
        "received_at",
        "completed_at",
    )
    list_filter = ("tbc",)
    search_fields = ("charger__charger_id", "request_id", "ocpp_message_id")
    readonly_fields = (
        "charger",
        "ocpp_message_id",
        "request_id",
        "tbc",
        "raw_payload",
        "received_at",
        "completed_at",
        "updated_at",
    )


@admin.register(DisplayMessage)
class DisplayMessageAdmin(admin.ModelAdmin):
    list_display = (
        "charger",
        "message_id",
        "priority",
        "state",
        "valid_from",
        "valid_to",
        "language",
        "created_at",
    )
    list_filter = ("priority", "state", "language")
    search_fields = ("charger__charger_id", "message_id", "content")
    readonly_fields = (
        "notification",
        "charger",
        "message_id",
        "priority",
        "state",
        "valid_from",
        "valid_to",
        "language",
        "content",
        "component_name",
        "component_instance",
        "variable_name",
        "variable_instance",
        "raw_payload",
        "created_at",
    )


class CPFirmwareDeploymentInline(admin.TabularInline):
    model = CPFirmwareDeployment
    extra = 0
    can_delete = False
    ordering = ("-requested_at",)
    readonly_fields = (
        "charger",
        "node",
        "status",
        "status_info",
        "status_timestamp",
        "retrieve_date",
        "retry_count",
        "retry_interval",
        "download_token",
        "download_token_expires_at",
        "downloaded_at",
        "requested_at",
        "completed_at",
        "ocpp_message_id",
    )
    show_change_link = True


def _format_failure_message(result: dict, *, action_label: str) -> str:
    error_code = str(result.get("error_code") or "").strip()
    error_description = str(result.get("error_description") or "").strip()
    details = result.get("error_details")
    parts: list[str] = []
    if error_code:
        parts.append(_("code=%(code)s") % {"code": error_code})
    if error_description:
        parts.append(
            _("description=%(description)s") % {"description": error_description}
        )
    if details:
        try:
            details_text = json.dumps(details, sort_keys=True, ensure_ascii=False)
        except TypeError:
            details_text = str(details)
        if details_text:
            parts.append(_("details=%(details)s") % {"details": details_text})
    if parts:
        return _("%(action)s failed: %(details)s") % {
            "action": action_label,
            "details": ", ".join(parts),
        }
    return _("%(action)s failed.") % {"action": action_label}


@admin.register(CPFirmware)
class CPFirmwareAdmin(EntityModelAdmin):
    list_display = (
        "name",
        "filename",
        "content_type",
        "payload_size",
        "downloaded_at",
        "source_node",
        "source_charger",
    )
    list_filter = ("source", "content_type")
    search_fields = (
        "name",
        "filename",
        "source_charger__charger_id",
        "source_charger__display_name",
    )
    readonly_fields = (
        "source",
        "source_node",
        "source_charger",
        "payload_size",
        "checksum",
        "download_vendor_id",
        "download_message_id",
        "downloaded_at",
        "created_at",
        "updated_at",
        "metadata",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "description",
                    "filename",
                    "content_type",
                    "payload_encoding",
                    "payload_size",
                    "checksum",
                )
            },
        ),
        (
            _("Source"),
            {
                "fields": (
                    "source",
                    "source_node",
                    "source_charger",
                    "download_vendor_id",
                    "download_message_id",
                    "downloaded_at",
                )
            },
        ),
        (
            _("Metadata"),
            {"fields": ("metadata", "created_at", "updated_at")},
        ),
    )
    actions = ["upload_evcs_firmware"]
    inlines = [CPFirmwareDeploymentInline]

    def _format_pending_failure(self, result: dict) -> str:
        return _format_failure_message(result, action_label=_("Update firmware"))

    def _dispatch_firmware_update(
        self,
        request,
        firmware: CPFirmware,
        charger: Charger,
        retrieve_date: datetime | None,
        retries: int | None,
        retry_interval: int | None,
    ) -> bool:
        connection = store.get_connection(charger.charger_id, charger.connector_id)
        if connection is None:
            self.message_user(
                request,
                _("%(charger)s is not currently connected to the platform.")
                % {"charger": charger},
                level=messages.ERROR,
            )
            return False

        if not firmware.has_binary and not firmware.has_json:
            self.message_user(
                request,
                _("%(firmware)s does not contain any payload to upload.")
                % {"firmware": firmware},
                level=messages.ERROR,
            )
            return False

        start_time = retrieve_date or (timezone.now() + timedelta(seconds=30))
        if timezone.is_naive(start_time):
            start_time = timezone.make_aware(
                start_time, timezone.get_current_timezone()
            )

        message_id = uuid.uuid4().hex
        deployment = CPFirmwareDeployment.objects.create(
            firmware=firmware,
            charger=charger,
            node=charger.node_origin,
            ocpp_message_id=message_id,
            status="Pending",
            status_info=_("Awaiting charge point response."),
            status_timestamp=timezone.now(),
            retrieve_date=start_time,
            retry_count=int(retries or 0),
            retry_interval=int(retry_interval or 0),
            request_payload={},
            is_user_data=True,
        )
        token = deployment.issue_download_token(lifetime=timedelta(hours=4))
        download_url = request.build_absolute_uri(
            reverse("ocpp:cp-firmware-download", args=[deployment.pk, token])
        )
        payload = {
            "location": download_url,
            "retrieveDate": start_time.isoformat(),
        }
        if retries is not None:
            payload["retries"] = int(retries)
        if retry_interval:
            payload["retryInterval"] = int(retry_interval)
        if firmware.checksum:
            payload["checksum"] = firmware.checksum
        deployment.request_payload = payload
        deployment.save(update_fields=["request_payload", "updated_at"])

        frame = json.dumps([2, message_id, "UpdateFirmware", payload])
        async_to_sync(connection.send)(frame)
        log_key = store.identity_key(charger.charger_id, charger.connector_id)
        store.add_log(
            log_key,
            _("Dispatched UpdateFirmware request."),
            log_type="charger",
        )
        store.register_pending_call(
            message_id,
            {
                "action": "UpdateFirmware",
                "charger_id": charger.charger_id,
                "connector_id": charger.connector_id,
                "deployment_pk": deployment.pk,
                "log_key": log_key,
            },
        )
        store.schedule_call_timeout(
            message_id, action="UpdateFirmware", log_key=log_key
        )

        result = store.wait_for_pending_call(message_id, timeout=15.0)
        if result is None:
            deployment.mark_status("Timeout", _("No response received."))
            deployment.completed_at = timezone.now()
            deployment.save(update_fields=["completed_at", "updated_at"])
            self.message_user(
                request,
                _(
                    "The charge point did not respond to the UpdateFirmware request."
                ),
                level=messages.ERROR,
            )
            return False
        if not result.get("success", True):
            detail = self._format_pending_failure(result)
            deployment.mark_status("Error", detail, response=result.get("payload"))
            deployment.completed_at = timezone.now()
            deployment.save(update_fields=["completed_at", "updated_at"])
            self.message_user(request, detail, level=messages.ERROR)
            return False

        payload_data = result.get("payload") or {}
        status_value = str(payload_data.get("status") or "").strip() or "Accepted"
        timestamp = timezone.now()
        deployment.mark_status(status_value, "", timestamp, response=payload_data)
        if status_value.lower() != "accepted":
            self.message_user(
                request,
                _(
                    "UpdateFirmware for %(charger)s was %(status)s."
                )
                % {"charger": charger, "status": status_value},
                level=messages.ERROR,
            )
            return False

        self.message_user(
            request,
            _("Queued firmware installation for %(charger)s.")
            % {"charger": charger},
            level=messages.SUCCESS,
        )
        return True

    @admin.action(description=_("Upload EVCS firmware"))
    def upload_evcs_firmware(self, request, queryset):
        selected_ids = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
        if selected_ids:
            firmware_qs = CPFirmware.objects.filter(pk__in=selected_ids)
            firmware_map = {str(obj.pk): obj for obj in firmware_qs}
            firmware_list = [
                firmware_map[value]
                for value in selected_ids
                if value in firmware_map
            ]
        else:
            firmware_list = list(queryset)
            selected_ids = [str(obj.pk) for obj in firmware_list]

        if not firmware_list:
            self.message_user(
                request,
                _("Select at least one firmware record to upload."),
                level=messages.ERROR,
            )
            return None

        form = UploadFirmwareForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            chargers = list(form.cleaned_data["chargers"])
            retrieve_date = form.cleaned_data.get("retrieve_date")
            retries = form.cleaned_data.get("retries")
            retry_interval = form.cleaned_data.get("retry_interval")
            success_count = 0
            for firmware in firmware_list:
                for charger in chargers:
                    if self._dispatch_firmware_update(
                        request,
                        firmware,
                        charger,
                        retrieve_date,
                        retries,
                        retry_interval,
                    ):
                        success_count += 1
            if success_count:
                self.message_user(
                    request,
                    ngettext(
                        "Queued %(count)d firmware upload.",
                        "Queued %(count)d firmware uploads.",
                        success_count,
                    )
                    % {"count": success_count},
                    level=messages.SUCCESS,
                )
            return None

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Upload EVCS firmware"),
            "firmware_list": firmware_list,
            "selected_ids": selected_ids,
            "action_name": request.POST.get("action", "upload_evcs_firmware"),
            "select_across": request.POST.get("select_across", "0"),
            "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
            "adminform": helpers.AdminForm(
                form,
                [
                    (
                        None,
                        {
                            "fields": (
                                "chargers",
                                "retrieve_date",
                                "retries",
                                "retry_interval",
                            )
                        },
                    )
                ],
                {},
            ),
            "form": form,
            "media": self.media + form.media,
        }
        return TemplateResponse(
            request, "admin/ocpp/cpfirmware/upload_evcs.html", context
        )


@admin.register(CPFirmwareDeployment)
class CPFirmwareDeploymentAdmin(EntityModelAdmin):
    list_display = (
        "firmware",
        "charger",
        "status",
        "status_timestamp",
        "requested_at",
        "completed_at",
    )
    list_filter = ("status",)
    search_fields = (
        "firmware__name",
        "charger__charger_id",
        "ocpp_message_id",
    )
    readonly_fields = (
        "firmware",
        "charger",
        "node",
        "ocpp_message_id",
        "status",
        "status_info",
        "status_timestamp",
        "requested_at",
        "completed_at",
        "retrieve_date",
        "retry_count",
        "retry_interval",
        "download_token",
        "download_token_expires_at",
        "downloaded_at",
        "request_payload",
        "response_payload",
        "created_at",
        "updated_at",
    )


class ChargingScheduleInline(admin.StackedInline):
    model = ChargingSchedule
    form = ChargingScheduleForm
    extra = 0
    min_num = 1
    max_num = 1


class ChargingProfileDispatchInline(admin.TabularInline):
    model = ChargingProfileDispatch
    extra = 0
    can_delete = False
    readonly_fields = (
        "charger",
        "message_id",
        "status",
        "status_info",
        "request_payload",
        "response_payload",
        "responded_at",
        "created_at",
        "updated_at",
    )
    fields = readonly_fields


@admin.register(ChargingProfile)
class ChargingProfileAdmin(EntityModelAdmin):
    actions = ("send_bundled_profile",)
    list_display = (
        "connector_id",
        "charging_profile_id",
        "purpose",
        "kind",
        "stack_level",
        "updated_at",
    )
    list_filter = ("purpose", "kind", "recurrency_kind")
    search_fields = ("charging_profile_id", "description")
    ordering = ("connector_id", "-stack_level", "charging_profile_id")
    inlines = (ChargingScheduleInline, ChargingProfileDispatchInline)
    readonly_fields = (
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("connector_id", "description")}),
        (
            _("Profile"),
            {
                "fields": (
                    "charging_profile_id",
                    "stack_level",
                    "purpose",
                    "kind",
                    "recurrency_kind",
                    "transaction_id",
                    "valid_from",
                    "valid_to",
                )
            },
        ),
        (_("Tracking"), {"fields": ("created_at", "updated_at")}),
    )

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        next_id = (
            ChargingProfile.objects.aggregate(Max("charging_profile_id"))["charging_profile_id__max"]
            or 0
        )
        initial.setdefault("charging_profile_id", next_id + 1)
        return initial

    @staticmethod
    def _combined_request_payload(
        profiles: list[ChargingProfile],
    ) -> tuple[dict[str, object] | None, str | None]:
        if not profiles:
            return None, "No profiles selected."

        first = profiles[0]
        for profile in profiles[1:]:
            if not getattr(profile, "schedule", None):
                return None, str(_("All profiles must have a schedule."))
            if (
                profile.purpose != first.purpose
                or profile.kind != first.kind
                or profile.recurrency_kind != first.recurrency_kind
                or profile.transaction_id != first.transaction_id
            ):
                return None, str(
                    _(
                        "Profiles must share the same purpose, kind, recurrency kind, and transaction to bundle."
                    )
                )
            if profile.schedule.charging_rate_unit != first.schedule.charging_rate_unit:
                return None, str(
                    _("Profiles must use the same charging rate unit to bundle together.")
                )

        if not getattr(first, "schedule", None):
            return None, str(_("Profiles must include a schedule."))

        periods: list[dict[str, object]] = []
        for profile in profiles:
            periods.extend(profile.schedule.charging_schedule_periods or [])

        periods.sort(key=lambda entry: entry.get("start_period", 0))
        schedule_payload = first.schedule.as_charging_schedule_payload(periods=periods)
        payload = first.as_set_charging_profile_request(
            connector_id=0, schedule_payload=schedule_payload
        )
        return payload, None

    def _validate_units(self, request, charger: Charger, schedule_unit: str | None) -> bool:
        if schedule_unit is None:
            return True
        if schedule_unit == ChargingProfile.RateUnit.AMP:
            return True
        charger_units = {Charger.EnergyUnit.W, Charger.EnergyUnit.KW}
        if charger.energy_unit in charger_units and schedule_unit != ChargingProfile.RateUnit.WATT:
            self.message_user(
                request,
                _(
                    "Use watt-based charging schedules when dispatching to %(charger)s to match its configured units."
                )
                % {"charger": charger},
                level=messages.ERROR,
            )
            return False
        return True

    def _send_profile_payload(
        self, request, charger: Charger, payload: dict[str, object]
    ) -> str | None:
        connector_value = 0
        if charger.is_local:
            ws = store.get_connection(charger.charger_id, connector_value)
            if ws is None:
                self.message_user(
                    request,
                    _("%(charger)s is not connected.") % {"charger": charger},
                    level=messages.ERROR,
                )
                return None

            message_id = uuid.uuid4().hex
            msg = json.dumps([2, message_id, "SetChargingProfile", payload])
            try:
                async_to_sync(ws.send)(msg)
            except Exception as exc:  # pragma: no cover - network error
                self.message_user(
                    request,
                    _(f"{charger}: failed to send SetChargingProfile ({exc})"),
                    level=messages.ERROR,
                )
                return None

            log_key = store.identity_key(charger.charger_id, connector_value)
            store.add_log(log_key, f"< {msg}", log_type="charger")
            store.register_pending_call(
                message_id,
                {
                    "action": "SetChargingProfile",
                    "charger_id": charger.charger_id,
                    "connector_id": connector_value,
                    "log_key": log_key,
                    "requested_at": timezone.now(),
                },
            )
            store.schedule_call_timeout(
                message_id,
                action="SetChargingProfile",
                log_key=log_key,
            )
            return message_id

        self.message_user(
            request,
            _("Remote profile dispatch is not available for this charger."),
            level=messages.ERROR,
        )
        return None

    @admin.action(description=_("Send bundled profile to EVCS"))
    def send_bundled_profile(self, request, queryset):
        profiles = list(queryset.select_related("schedule"))
        if not profiles:
            self.message_user(
                request,
                _("Select at least one charging profile to dispatch."),
                level=messages.ERROR,
            )
            return None

        selected_ids = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
        if "apply" in request.POST:
            form = ChargingProfileSendForm(request.POST)
            if form.is_valid():
                payload, error = self._combined_request_payload(profiles)
                if error:
                    self.message_user(request, error, level=messages.ERROR)
                    return None
                charger = form.cleaned_data["charger"]
                schedule_unit = (
                    payload.get("csChargingProfiles", {})
                    .get("chargingSchedule", {})
                    .get("chargingRateUnit")
                )
                if not self._validate_units(request, charger, schedule_unit):
                    return None
                message_id = self._send_profile_payload(request, charger, payload)
                if message_id:
                    for profile in profiles:
                        ChargingProfileDispatch.objects.create(
                            profile=profile,
                            charger=charger,
                            message_id=message_id,
                            request_payload=payload,
                            status="Pending",
                        )
                    self.message_user(
                        request,
                        ngettext(
                            "Queued %(count)d profile for %(charger)s.",
                            "Queued %(count)d profiles for %(charger)s.",
                            len(profiles),
                        )
                        % {"count": len(profiles), "charger": charger},
                        level=messages.SUCCESS,
                    )
                return None
        else:
            form = ChargingProfileSendForm()

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Send charging profile to EVCS"),
            "profiles": profiles,
            "selected_ids": selected_ids,
            "action_name": request.POST.get("action", "send_bundled_profile"),
            "select_across": request.POST.get("select_across", "0"),
            "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
            "adminform": helpers.AdminForm(
                form,
                [(None, {"fields": ("charger",)})],
                {},
            ),
            "form": form,
            "media": self.media + form.media,
        }
        return TemplateResponse(
            request, "admin/ocpp/chargingprofile/send.html", context
        )


@admin.register(CPReservation)
class CPReservationAdmin(EntityModelAdmin):
    form = CPReservationForm
    actions = ("cancel_reservations",)
    list_display = (
        "location",
        "connector_side_display",
        "start_time",
        "end_time_display",
        "account",
        "id_tag_display",
        "evcs_status",
        "evcs_confirmed",
    )
    list_filter = ("location", "evcs_confirmed")
    search_fields = (
        "location__name",
        "connector__charger_id",
        "connector__display_name",
        "account__name",
        "id_tag",
        "rfid__rfid",
    )
    date_hierarchy = "start_time"
    ordering = ("-start_time",)
    autocomplete_fields = ("location", "account", "rfid")
    readonly_fields = (
        "connector_identity",
        "connector_side_display",
        "evcs_status",
        "evcs_error",
        "evcs_confirmed",
        "evcs_confirmed_at",
        "ocpp_message_id",
        "created_on",
        "updated_on",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "location",
                    "account",
                    "rfid",
                    "id_tag",
                    "start_time",
                    "duration_minutes",
                )
            },
        ),
        (
            _("Assigned connector"),
            {"fields": ("connector_identity", "connector_side_display")},
        ),
        (
            _("EVCS response"),
            {
                "fields": (
                    "evcs_confirmed",
                    "evcs_status",
                    "evcs_confirmed_at",
                    "evcs_error",
                    "ocpp_message_id",
                )
            },
        ),
        (
            _("Metadata"),
            {"fields": ("created_on", "updated_on")},
        ),
    )
    def save_model(self, request, obj, form, change):
        trigger_fields = {
            "start_time",
            "duration_minutes",
            "location",
            "id_tag",
            "rfid",
            "account",
        }
        changed_data = set(getattr(form, "changed_data", []))
        should_send = not change or bool(trigger_fields.intersection(changed_data))
        with transaction.atomic():
            super().save_model(request, obj, form, change)
            if should_send:
                try:
                    obj.send_reservation_request()
                except ValidationError as exc:
                    raise ValidationError(exc.message_dict or exc.messages or str(exc))
                else:
                    self.message_user(
                        request,
                        _("Reservation request sent to %(connector)s.")
                        % {"connector": self.connector_identity(obj)},
                        messages.SUCCESS,
                    )

    @admin.display(description=_("Connector"), ordering="connector__connector_id")
    def connector_side_display(self, obj):
        return obj.connector_label or "-"

    @admin.display(description=_("Connector identity"))
    def connector_identity(self, obj):
        if obj.connector_id:
            return obj.connector.identity_slug()
        return "-"

    @admin.display(description=_("End time"))
    def end_time_display(self, obj):
        try:
            value = timezone.localtime(obj.end_time)
        except Exception:
            value = obj.end_time
        if not value:
            return "-"
        return formats.date_format(value, "DATETIME_FORMAT")

    @admin.display(description=_("Id tag"))
    def id_tag_display(self, obj):
        value = obj.id_tag_value
        return value or "-"

    @admin.action(description=_("Cancel selected Reservations"))
    def cancel_reservations(self, request, queryset):
        cancelled = 0
        for reservation in queryset:
            try:
                reservation.send_cancel_request()
            except ValidationError as exc:
                messages_list: list[str] = []
                if getattr(exc, "message_dict", None):
                    for errors in exc.message_dict.values():
                        messages_list.extend(str(error) for error in errors)
                elif getattr(exc, "messages", None):
                    messages_list.extend(str(error) for error in exc.messages)
                else:
                    messages_list.append(str(exc))
                for message in messages_list:
                    self.message_user(
                        request,
                        _("%(reservation)s: %(message)s")
                        % {"reservation": reservation, "message": message},
                        level=messages.ERROR,
                    )
            except Exception as exc:  # pragma: no cover - defensive
                self.message_user(
                    request,
                    _("%(reservation)s: unable to cancel reservation (%(error)s)")
                    % {"reservation": reservation, "error": exc},
                    level=messages.ERROR,
                )
            else:
                cancelled += 1
        if cancelled:
            self.message_user(
                request,
                ngettext(
                    "Sent %(count)d cancellation request.",
                    "Sent %(count)d cancellation requests.",
                    cancelled,
                )
                % {"count": cancelled},
                level=messages.SUCCESS,
            )


@admin.register(PowerProjection)
class PowerProjectionAdmin(EntityModelAdmin):
    list_display = (
        "charger",
        "connector_id",
        "status",
        "schedule_start",
        "duration_seconds",
        "received_at",
    )
    list_filter = ("status",)
    search_fields = ("charger__charger_id", "charger__display_name")
    ordering = ("-received_at", "-requested_at")
    autocomplete_fields = ("charger",)
    readonly_fields = ("raw_response", "requested_at", "received_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("charger", "connector_id", "status")}),
        (
            _("Schedule"),
            {
                "fields": (
                    "schedule_start",
                    "duration_seconds",
                    "charging_rate_unit",
                    "charging_schedule_periods",
                )
            },
        ),
        (
            _("Response"),
            {
                "fields": (
                    "raw_response",
                    "requested_at",
                    "received_at",
                    "updated_at",
                )
            },
        ),
    )


@admin.register(Simulator)
class SimulatorAdmin(SaveBeforeChangeAction, LogViewAdminMixin, EntityModelAdmin):
    list_display = (
        "name",
        "default",
        "host",
        "ws_port",
        "ws_url",
        "interval",
        "average_kwh_display",
        "amperage",
        "running",
        "log_link",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "default",
                    "name",
                    "cp_path",
                    ("host", "ws_port"),
                    "rfid",
                    ("duration", "interval", "pre_charge_delay"),
                    ("average_kwh", "amperage"),
                    ("repeat", "door_open"),
                    ("username", "password"),
                )
            },
        ),
        (
            "Configuration",
            {
                "fields": ("configuration",),
                "classes": ("collapse",),
                "description": (
                    "Select a CP Configuration to reuse for GetConfiguration responses."
                ),
            },
        ),
    )
    actions = (
        "start_simulator",
        "stop_simulator",
        "send_open_door",
    )
    changelist_actions = ["start_default_simulator"]
    change_actions = ["start_simulator_action", "stop_simulator_action"]

    log_type = "simulator"

    @admin.display(description="Average kWh", ordering="average_kwh")
    def average_kwh_display(self, obj):
        """Display ``average_kwh`` with a dot decimal separator for Spanish locales."""

        language = translation.get_language() or ""
        if language.startswith("es"):
            return formats.number_format(
                obj.average_kwh,
                decimal_pos=2,
                use_l10n=False,
                force_grouping=False,
            )

        return formats.number_format(
            obj.average_kwh,
            decimal_pos=2,
            use_l10n=True,
            force_grouping=False,
        )

    def save_model(self, request, obj, form, change):
        previous_door_open = False
        if change and obj.pk:
            previous_door_open = (
                type(obj)
                .objects.filter(pk=obj.pk)
                .values_list("door_open", flat=True)
                .first()
                or False
            )
        super().save_model(request, obj, form, change)
        if obj.door_open and not previous_door_open:
            triggered = self._queue_door_open(request, obj)
            if not triggered:
                type(obj).objects.filter(pk=obj.pk).update(door_open=False)
                obj.door_open = False

    def _queue_door_open(self, request, obj) -> bool:
        sim = store.simulators.get(obj.pk)
        if not sim:
            self.message_user(
                request,
                f"{obj.name}: simulator is not running",
                level=messages.ERROR,
            )
            return False
        type(obj).objects.filter(pk=obj.pk).update(door_open=True)
        obj.door_open = True
        store.add_log(
            obj.cp_path,
            "Door open event requested from admin",
            log_type="simulator",
        )
        if hasattr(sim, "trigger_door_open"):
            sim.trigger_door_open()
        else:  # pragma: no cover - unexpected condition
            self.message_user(
                request,
                f"{obj.name}: simulator cannot send door open event",
                level=messages.ERROR,
            )
            type(obj).objects.filter(pk=obj.pk).update(door_open=False)
            obj.door_open = False
            return False
        type(obj).objects.filter(pk=obj.pk).update(door_open=False)
        obj.door_open = False
        self.message_user(
            request,
            f"{obj.name}: DoorOpen status notification sent",
        )
        return True

    def running(self, obj):
        return obj.pk in store.simulators

    running.boolean = True

    @admin.action(description="Send Open Door")
    def send_open_door(self, request, queryset):
        for obj in queryset:
            self._queue_door_open(request, obj)

    def _start_simulators(self, request, queryset):
        from django.urls import reverse
        from django.utils.html import format_html

        for obj in queryset:
            if obj.pk in store.simulators:
                self.message_user(request, f"{obj.name}: already running")
                continue
            type(obj).objects.filter(pk=obj.pk).update(door_open=False)
            obj.door_open = False
            store.register_log_name(obj.cp_path, obj.name, log_type="simulator")
            sim = ChargePointSimulator(obj.as_config())
            started, status, log_file = sim.start()
            if started:
                store.simulators[obj.pk] = sim
            log_url = reverse("admin:ocpp_simulator_log", args=[obj.pk])
            self.message_user(
                request,
                format_html(
                    '{}: {}. Log: <code>{}</code> (<a href="{}" target="_blank">View Log</a>)',
                    obj.name,
                    status,
                    log_file,
                    log_url,
                ),
            )

    @admin.action(description="Start selected simulators")
    def start_simulator(self, request, queryset):
        self._start_simulators(request, queryset)

    @admin.action(description="Start Default Simulator")
    def start_default_simulator(self, request, queryset=None):
        from django.urls import reverse
        from django.utils.html import format_html

        default_simulator = (
            Simulator.objects.filter(default=True, is_deleted=False).order_by("pk").first()
        )
        if default_simulator is None:
            self.message_user(
                request,
                "No default simulator is configured.",
                level=messages.ERROR,
            )
        else:
            if default_simulator.pk in store.simulators:
                self.message_user(
                    request,
                    f"{default_simulator.name}: already running",
                )
            else:
                type(default_simulator).objects.filter(pk=default_simulator.pk).update(
                    door_open=False
                )
                default_simulator.door_open = False
                store.register_log_name(
                    default_simulator.cp_path, default_simulator.name, log_type="simulator"
                )
                simulator = ChargePointSimulator(default_simulator.as_config())
                started, status, log_file = simulator.start()
                if started:
                    store.simulators[default_simulator.pk] = simulator
                log_url = reverse("admin:ocpp_simulator_log", args=[default_simulator.pk])
                self.message_user(
                    request,
                    format_html(
                        '{}: {}. Log: <code>{}</code> (<a href="{}" target="_blank">View Log</a>)',
                        default_simulator.name,
                        status,
                        log_file,
                    log_url,
                ),
            )

        return HttpResponseRedirect(reverse("admin:ocpp_simulator_changelist"))

    start_default_simulator.label = _("Start Default Simulator")
    start_default_simulator.requires_queryset = False

    def stop_simulator(self, request, queryset):
        async def _stop(objs):
            for obj in objs:
                sim = store.simulators.pop(obj.pk, None)
                if sim:
                    await sim.stop()

        objs = list(queryset)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_stop(objs))
        else:
            loop.create_task(_stop(objs))
        self.message_user(request, "Stopping simulators")

    stop_simulator.short_description = "Stop selected simulators"

    def start_simulator_action(self, request, obj):
        queryset = type(obj).objects.filter(pk=obj.pk)
        self.start_simulator(request, queryset)

    def stop_simulator_action(self, request, obj):
        queryset = type(obj).objects.filter(pk=obj.pk)
        self.stop_simulator(request, queryset)

    def response_action(self, request, queryset):
        if request.POST.get("action") == "start_default_simulator":
            return self.start_default_simulator(request)
        return super().response_action(request, queryset)

    def log_link(self, obj):
        from django.utils.html import format_html
        from django.urls import reverse

        url = reverse("admin:ocpp_simulator_log", args=[obj.pk])
        return format_html('<a href="{}" target="_blank">view</a>', url)

    log_link.short_description = "Log"

    def get_log_identifier(self, obj):
        return obj.cp_path


class MeterValueInline(admin.TabularInline):
    model = MeterValue
    extra = 0
    fields = (
        "timestamp",
        "context",
        "energy",
        "voltage",
        "current_import",
        "current_offered",
        "temperature",
        "soc",
        "connector_id",
    )
    readonly_fields = fields
    can_delete = False


@admin.register(Transaction)
class TransactionAdmin(EntityModelAdmin):
    change_list_template = "admin/ocpp/transaction/change_list.html"
    list_display = (
        "charger",
        "connector_number",
        "account",
        "rfid",
        "vid",
        "meter_start",
        "meter_stop",
        "start_time",
        "stop_time",
        "kw",
    )
    readonly_fields = ("kw", "received_start_time", "received_stop_time")
    list_filter = ("charger", "account")
    date_hierarchy = "start_time"
    inlines = [MeterValueInline]

    def connector_number(self, obj):
        return obj.connector_id or ""

    connector_number.short_description = "#"
    connector_number.admin_order_field = "connector_id"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "export/",
                self.admin_site.admin_view(self.export_view),
                name="ocpp_transaction_export",
            ),
            path(
                "import/",
                self.admin_site.admin_view(self.import_view),
                name="ocpp_transaction_import",
            ),
        ]
        return custom + urls

    def export_view(self, request):
        if request.method == "POST":
            form = TransactionExportForm(request.POST)
            if form.is_valid():
                chargers = form.cleaned_data["chargers"]
                data = export_transactions(
                    start=form.cleaned_data["start"],
                    end=form.cleaned_data["end"],
                    chargers=[c.charger_id for c in chargers] if chargers else None,
                )
                response = HttpResponse(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    content_type="application/json",
                )
                response["Content-Disposition"] = (
                    "attachment; filename=transactions.json"
                )
                return response
        else:
            form = TransactionExportForm()
        context = self.admin_site.each_context(request)
        context["form"] = form
        return TemplateResponse(request, "admin/ocpp/transaction/export.html", context)

    def import_view(self, request):
        if request.method == "POST":
            form = TransactionImportForm(request.POST, request.FILES)
            if form.is_valid():
                data = json.load(form.cleaned_data["file"])
                imported = import_transactions_data(data)
                self.message_user(request, f"Imported {imported} transactions")
                return HttpResponseRedirect("../")
        else:
            form = TransactionImportForm()
        context = self.admin_site.each_context(request)
        context["form"] = form
        return TemplateResponse(request, "admin/ocpp/transaction/import.html", context)


class MeterValueDateFilter(admin.SimpleListFilter):
    title = "Timestamp"
    parameter_name = "timestamp_range"

    def lookups(self, request, model_admin):
        return [
            ("today", "Today"),
            ("7days", "Last 7 days"),
            ("30days", "Last 30 days"),
            ("older", "Older than 30 days"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        now = timezone.now()
        if value == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            return queryset.filter(timestamp__gte=start, timestamp__lt=end)
        if value == "7days":
            start = now - timedelta(days=7)
            return queryset.filter(timestamp__gte=start)
        if value == "30days":
            start = now - timedelta(days=30)
            return queryset.filter(timestamp__gte=start)
        if value == "older":
            cutoff = now - timedelta(days=30)
            return queryset.filter(timestamp__lt=cutoff)
        return queryset


@admin.register(MeterValue)
class MeterValueAdmin(EntityModelAdmin):
    list_display = (
        "charger",
        "timestamp",
        "context",
        "energy",
        "voltage",
        "current_import",
        "current_offered",
        "temperature",
        "soc",
        "connector_id",
        "transaction",
    )
    date_hierarchy = "timestamp"
    list_filter = ("charger", MeterValueDateFilter)


@admin.register(SecurityEvent)
class SecurityEventAdmin(EntityModelAdmin):
    list_display = (
        "charger",
        "event_type",
        "event_timestamp",
        "trigger",
        "sequence_number",
    )
    list_filter = ("event_type",)
    search_fields = ("charger__charger_id", "event_type", "tech_info")
    date_hierarchy = "event_timestamp"


@admin.register(ChargerLogRequest)
class ChargerLogRequestAdmin(EntityModelAdmin):
    list_display = (
        "charger",
        "request_id",
        "log_type",
        "status",
        "last_status_at",
        "requested_at",
        "responded_at",
    )
    list_filter = ("log_type", "status")
    search_fields = (
        "charger__charger_id",
        "log_type",
        "status",
        "filename",
        "location",
    )
    date_hierarchy = "requested_at"
class CPForwarderForm(forms.ModelForm):
    forwarded_messages = forms.MultipleChoiceField(
        label=_("Forwarded messages"),
        choices=[
            (message, message)
            for message in CPForwarder.available_forwarded_messages()
        ],
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text=_(
            "Choose which OCPP messages should be forwarded. Only charge points "
            "with Export transactions enabled are eligible."
        ),
    )

    class Meta:
        model = CPForwarder
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        initial = CPForwarder.available_forwarded_messages()
        if self.instance and self.instance.pk:
            initial = self.instance.get_forwarded_messages()
        self.fields["forwarded_messages"].initial = initial

    def clean_forwarded_messages(self):
        selected = self.cleaned_data.get("forwarded_messages") or []
        return CPForwarder.sanitize_forwarded_messages(selected)


@admin.register(CPForwarder)
class CPForwarderAdmin(EntityModelAdmin):
    form = CPForwarderForm
    list_display = (
        "display_name",
        "target_node",
        "enabled",
        "is_running",
        "last_forwarded_at",
        "last_status",
        "last_error",
    )
    list_filter = ("enabled", "is_running", "target_node")
    search_fields = (
        "name",
        "target_node__hostname",
        "target_node__public_endpoint",
        "target_node__address",
    )
    autocomplete_fields = ["target_node", "source_node"]
    readonly_fields = (
        "is_running",
        "last_forwarded_at",
        "last_status",
        "last_error",
        "last_synced_at",
        "created_at",
        "updated_at",
    )
    actions = [
        "enable_forwarders",
        "disable_forwarders",
        "enable_export_transactions",
        "disable_export_transactions",
        "test_forwarders",
    ]

    fieldsets = (
        (
            None,
            {
                "description": _(
                    "Only charge points with Export transactions enabled will be "
                    "forwarded by this configuration."
                ),
                "fields": (
                    "name",
                    "source_node",
                    "target_node",
                    "enabled",
                    "is_running",
                    "last_forwarded_at",
                    "last_status",
                    "last_error",
                    "last_synced_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
        (
            _("Forwarding"),
            {
                "classes": ("collapse",),
                "fields": ("forwarded_messages",),
            },
        ),
    )

    @admin.display(description=_("Name"))
    def display_name(self, obj):
        if obj.name:
            return obj.name
        if obj.target_node:
            return str(obj.target_node)
        return _("Forwarder")

    def _chargers_for_forwarder(self, forwarder):
        from apps.ocpp.models import Charger

        queryset = Charger.objects.all()
        source_node = forwarder.source_node or Node.get_local()
        if source_node and source_node.pk:
            queryset = queryset.filter(
                Q(node_origin=source_node) | Q(node_origin__isnull=True)
            )
        return queryset

    def _toggle_forwarders(self, request, queryset, enabled: bool) -> None:
        if not queryset.exists():
            self.message_user(
                request,
                _("No forwarders were selected."),
                messages.WARNING,
            )
            return
        queryset.update(enabled=enabled)
        synced = 0
        failed = 0
        for forwarder in queryset:
            try:
                forwarder.sync_chargers()
                synced += 1
            except Exception as exc:
                failed += 1
                self.message_user(
                    request,
                    _("Failed to sync forwarder %(name)s: %(error)s")
                    % {"name": forwarder, "error": exc},
                    messages.ERROR,
                )
        if synced:
            self.message_user(
                request,
                _("Updated %(count)s forwarder(s).") % {"count": synced},
                messages.SUCCESS,
            )
        if failed:
            self.message_user(
                request,
                _("Failed to update %(count)s forwarder(s).") % {"count": failed},
                messages.ERROR,
            )

    def _toggle_export_transactions(self, request, queryset, enabled: bool) -> None:
        if not queryset.exists():
            self.message_user(
                request,
                _("No forwarders were selected."),
                messages.WARNING,
            )
            return
        updated = 0
        for forwarder in queryset:
            chargers = self._chargers_for_forwarder(forwarder)
            updated += chargers.update(export_transactions=enabled)
            try:
                forwarder.sync_chargers()
            except Exception as exc:
                self.message_user(
                    request,
                    _("Failed to sync forwarder %(name)s: %(error)s")
                    % {"name": forwarder, "error": exc},
                    messages.ERROR,
                )
        self.message_user(
            request,
            _("Updated export settings for %(count)s charge point(s).")
            % {"count": updated},
            messages.SUCCESS,
        )

    @admin.action(description=_("Enable selected forwarders"))
    def enable_forwarders(self, request, queryset):
        self._toggle_forwarders(request, queryset, True)

    @admin.action(description=_("Disable selected forwarders"))
    def disable_forwarders(self, request, queryset):
        self._toggle_forwarders(request, queryset, False)

    @admin.action(description=_("Enable export transactions for charge points"))
    def enable_export_transactions(self, request, queryset):
        self._toggle_export_transactions(request, queryset, True)

    @admin.action(description=_("Disable export transactions for charge points"))
    def disable_export_transactions(self, request, queryset):
        self._toggle_export_transactions(request, queryset, False)

    @admin.action(description=_("Test forwarder configuration"))
    def test_forwarders(self, request, queryset):
        tested = 0
        for forwarder in queryset:
            forwarder.sync_chargers()
            tested += 1
        if tested:
            self.message_user(
                request,
                _("Tested %(count)s forwarder(s).") % {"count": tested},
                messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                _("No forwarders were selected."),
                messages.WARNING,
            )


@admin.register(StationModel)
class StationModelAdmin(EntityModelAdmin):
    list_display = (
        "vendor",
        "model_family",
        "model",
        "preferred_ocpp_version",
        "max_power_kw",
        "max_voltage_v",
    )
    search_fields = ("vendor", "model_family", "model")
    list_filter = ("preferred_ocpp_version",)


@admin.register(CPNetworkProfile)
class CPNetworkProfileAdmin(EntityModelAdmin):
    list_display = (
        "name",
        "configuration_slot",
        "created_at",
        "updated_at",
    )
    list_filter = ("configuration_slot",)
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")


@admin.register(CPNetworkProfileDeployment)
class CPNetworkProfileDeploymentAdmin(EntityModelAdmin):
    list_display = (
        "network_profile",
        "charger",
        "status",
        "status_timestamp",
        "requested_at",
        "completed_at",
    )
    list_filter = ("status",)
    search_fields = (
        "network_profile__name",
        "charger__charger_id",
        "ocpp_message_id",
    )
    readonly_fields = ("requested_at", "created_at", "updated_at")


@admin.register(CPFirmwareRequest)
class CPFirmwareRequestAdmin(EntityModelAdmin):
    list_display = (
        "charger",
        "connector_id",
        "vendor_id",
        "status",
        "requested_at",
        "responded_at",
    )
    list_filter = ("status",)
    search_fields = ("charger__charger_id", "vendor_id")
    readonly_fields = ("requested_at", "updated_at")


@admin.register(RFIDSessionAttempt)
class RFIDSessionAttemptAdmin(EntityModelAdmin):
    list_display = (
        "rfid",
        "status",
        "charger",
        "account",
        "transaction",
        "attempted_at",
    )
    list_filter = ("status",)
    search_fields = (
        "rfid",
        "charger__charger_id",
        "account__name",
        "transaction__ocpp_id",
    )
    readonly_fields = ("attempted_at",)

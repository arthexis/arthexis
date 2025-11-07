from django.contrib import admin, messages
from django.contrib.admin import helpers
from django import forms

import asyncio
import base64
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlparse, unquote
import contextlib
import json
from typing import Any
from django.shortcuts import redirect
from django.utils import formats, timezone, translation
from django.utils.translation import gettext_lazy as _, ngettext
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html, format_html_join
from django.utils.text import slugify
from django.urls import path, reverse
from django.http import HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse

import uuid
from asgiref.sync import async_to_sync
import requests
from requests import RequestException
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from django.db import transaction
from django.core.exceptions import ValidationError
from django.conf import settings

from .models import (
    Charger,
    ChargerConfiguration,
    ConfigurationKey,
    Simulator,
    MeterValue,
    Transaction,
    Location,
    DataTransferMessage,
    CPReservation,
    CPFirmware,
    CPFirmwareDeployment,
)
from .simulator import ChargePointSimulator
from . import store
from .transactions_io import (
    export_transactions,
    import_transactions as import_transactions_data,
)
from .status_display import STATUS_BADGE_MAP, ERROR_OK_VALUES
from .views import _charger_state, _live_sessions
from core.admin import SaveBeforeChangeAction
from core.models import RFID as CoreRFID
from core.user_data import EntityModelAdmin
from nodes.models import Node


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


class TransactionExportForm(forms.Form):
    start = forms.DateTimeField(required=False)
    end = forms.DateTimeField(required=False)
    chargers = forms.ModelMultipleChoiceField(
        queryset=Charger.objects.all(), required=False
    )


class TransactionImportForm(forms.Form):
    file = forms.FileField()


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
            self.message_user(request, "Log is not available.", messages.ERROR)
            return redirect("..")
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
    readonly_fields = (
        "position",
        "key",
        "readonly",
        "value_display",
        "extra_display",
    )
    fields = readonly_fields
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
    list_display = (
        "charger_identifier",
        "connector_display",
        "origin_display",
        "created_at",
    )
    list_filter = ("connector_id",)
    search_fields = ("charger_identifier",)
    readonly_fields = (
        "charger_identifier",
        "connector_id",
        "origin_display",
        "evcs_snapshot_at",
        "created_at",
        "updated_at",
        "linked_chargers",
        "unknown_keys_display",
        "raw_payload_display",
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
                    "raw_payload_display",
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
    def raw_payload_display(self, obj):
        return self._render_json(obj.raw_payload)

    @admin.display(description="Origin")
    def origin_display(self, obj):
        if obj.evcs_snapshot_at:
            return "EVCS"
        return "Local"

    def save_model(self, request, obj, form, change):
        obj.evcs_snapshot_at = None
        super().save_model(request, obj, form, change)


@admin.register(ConfigurationKey)
class ConfigurationKeyAdmin(admin.ModelAdmin):
    list_display = ("configuration", "key", "position", "readonly")
    ordering = ("configuration", "position", "id")

    def get_model_perms(self, request):  # pragma: no cover - admin hook
        return {}


@admin.register(Location)
class LocationAdmin(EntityModelAdmin):
    form = LocationAdminForm
    list_display = ("name", "zone", "contract_type", "latitude", "longitude")
    change_form_template = "admin/ocpp/location/change_form.html"
    search_fields = ("name",)


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
            reverse("cp-firmware-download", args=[deployment.pk, token])
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


@admin.register(Charger)
class ChargerAdmin(LogViewAdminMixin, EntityModelAdmin):
    _REMOTE_DATETIME_FIELDS = {
        "availability_state_updated_at",
        "availability_requested_at",
        "availability_request_status_at",
        "last_online_at",
    }

    fieldsets = (
        (
            "General",
            {
                "fields": (
                    "charger_id",
                    "display_name",
                    "connector_id",
                    "language",
                    "location",
                    "last_path",
                    "last_heartbeat",
                    "last_meter_values",
                )
            },
        ),
        (
            "Firmware",
            {
                "fields": (
                    "firmware_status",
                    "firmware_status_info",
                    "firmware_timestamp",
                )
            },
        ),
        (
            "Diagnostics",
            {
                "fields": (
                    "diagnostics_status",
                    "diagnostics_timestamp",
                    "diagnostics_location",
                )
            },
        ),
        (
            "Availability",
            {
                "fields": (
                    "availability_state",
                    "availability_state_updated_at",
                    "availability_requested_state",
                    "availability_requested_at",
                    "availability_request_status",
                    "availability_request_status_at",
                    "availability_request_details",
                )
            },
        ),
        (
            "Configuration",
            {"fields": ("public_display", "require_rfid", "configuration")},
        ),
        (
            "Local authorization",
            {
                "fields": (
                    "local_auth_list_version",
                    "local_auth_list_updated_at",
                )
            },
        ),
        (
            "Network",
            {
                "fields": (
                    "node_origin",
                    "manager_node",
                    "forwarded_to",
                    "forwarding_watermark",
                    "allow_remote",
                    "export_transactions",
                    "last_online_at",
                )
            },
        ),
        (
            "References",
            {
                "fields": ("reference",),
            },
        ),
        (
            "Owner",
            {
                "fields": ("owner_users", "owner_groups"),
                "classes": ("collapse",),
            },
        ),
    )
    readonly_fields = (
        "last_heartbeat",
        "last_meter_values",
        "firmware_status",
        "firmware_status_info",
        "firmware_timestamp",
        "availability_state",
        "availability_state_updated_at",
        "availability_requested_state",
        "availability_requested_at",
        "availability_request_status",
        "availability_request_status_at",
        "availability_request_details",
        "configuration",
        "local_auth_list_version",
        "local_auth_list_updated_at",
        "forwarded_to",
        "forwarding_watermark",
        "last_online_at",
    )
    list_display = (
        "display_name_with_fallback",
        "connector_number",
        "charger_name_display",
        "local_indicator",
        "require_rfid_display",
        "public_display",
        "last_heartbeat",
        "today_kw",
        "total_kw_display",
        "page_link",
        "log_link",
        "status_link",
    )
    search_fields = ("charger_id", "connector_id", "location__name")
    filter_horizontal = ("owner_users", "owner_groups")
    actions = [
        "purge_data",
        "fetch_cp_configuration",
        "toggle_rfid_authentication",
        "send_rfid_list_to_evcs",
        "update_rfids_from_evcs",
        "recheck_charger_status",
        "get_diagnostics",
        "change_availability_operative",
        "change_availability_inoperative",
        "set_availability_state_operative",
        "set_availability_state_inoperative",
        "clear_authorization_cache",
        "remote_stop_transaction",
        "reset_chargers",
        "delete_selected",
    ]

    class DiagnosticsDownloadError(Exception):
        """Raised when diagnostics downloads fail."""

    def _diagnostics_directory_for(self, user) -> tuple[Path, Path]:
        username = getattr(user, "get_username", None)
        if callable(username):
            username = username()
        else:
            username = getattr(user, "username", "")
        if not username:
            username = str(getattr(user, "pk", "user"))
        username_component = Path(str(username)).name or "user"
        base_dir = Path(settings.BASE_DIR)
        user_dir = base_dir / "work" / username_component
        diagnostics_dir = user_dir / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        return diagnostics_dir, user_dir

    def _content_disposition_filename(self, header_value: str) -> str:
        for part in header_value.split(";"):
            candidate = part.strip()
            lower = candidate.lower()
            if lower.startswith("filename*="):
                value = candidate.split("=", 1)[1].strip()
                if value.lower().startswith("utf-8''"):
                    value = value[7:]
                return Path(unquote(value.strip('"'))).name
            if lower.startswith("filename="):
                value = candidate.split("=", 1)[1].strip().strip('"')
                return Path(value).name
        return ""

    def _diagnostics_filename(self, charger: Charger, location: str, response) -> str:
        parsed = urlparse(location)
        candidate = Path(parsed.path or "").name
        header_name = ""
        content_disposition = response.headers.get("Content-Disposition") if hasattr(response, "headers") else None
        if content_disposition:
            header_name = self._content_disposition_filename(content_disposition)
        if header_name:
            candidate = header_name
        if not candidate:
            candidate = "diagnostics.log"
        path_candidate = Path(candidate)
        suffix = "".join(path_candidate.suffixes)
        if suffix:
            base_name = candidate[: -len(suffix)]
        else:
            base_name = candidate
            suffix = ".log"
        base_name = base_name.rstrip(".")
        if not base_name:
            base_name = "diagnostics"
        charger_slug = slugify(charger.charger_id or charger.display_name or str(charger.pk or "charger"))
        if not charger_slug:
            charger_slug = "charger"
        diagnostics_slug = slugify(base_name) or "diagnostics"
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        return f"{charger_slug}-{diagnostics_slug}-{timestamp}{suffix}"

    def _unique_diagnostics_path(self, directory: Path, filename: str) -> Path:
        base_path = Path(filename)
        suffix = "".join(base_path.suffixes)
        if suffix:
            base_name = filename[: -len(suffix)]
        else:
            base_name = filename
            suffix = ""
        base_name = base_name.rstrip(".") or "diagnostics"
        candidate = directory / f"{base_name}{suffix}"
        counter = 1
        while candidate.exists():
            candidate = directory / f"{base_name}-{counter}{suffix}"
            counter += 1
        return candidate

    def _download_diagnostics(
        self,
        request,
        charger: Charger,
        location: str,
        diagnostics_dir: Path,
        user_dir: Path,
    ) -> tuple[Path, str]:
        parsed = urlparse(location)
        scheme = (parsed.scheme or "").lower()
        if scheme not in {"http", "https"}:
            raise self.DiagnosticsDownloadError(
                _("Diagnostics location must use HTTP or HTTPS.")
            )
        try:
            response = requests.get(location, stream=True, timeout=15)
        except RequestException as exc:
            raise self.DiagnosticsDownloadError(
                _("Failed to download diagnostics: %s") % exc
            ) from exc
        try:
            if response.status_code != 200:
                raise self.DiagnosticsDownloadError(
                    _("Diagnostics download returned status %s.")
                    % response.status_code
                )
            filename = self._diagnostics_filename(charger, location, response)
            destination = self._unique_diagnostics_path(diagnostics_dir, filename)
            try:
                with destination.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        handle.write(chunk)
            except OSError as exc:
                raise self.DiagnosticsDownloadError(
                    _("Unable to write diagnostics file: %s") % exc
                ) from exc
        finally:
            with contextlib.suppress(Exception):
                response.close()
        relative_asset = destination.relative_to(user_dir).as_posix()
        asset_url = reverse(
            "pages:readme-asset",
            kwargs={"source": "work", "asset": relative_asset},
        )
        absolute_url = request.build_absolute_uri(asset_url)
        return destination, absolute_url

    def _prepare_remote_credentials(self, request):
        local = Node.get_local()
        if not local or not local.uuid:
            self.message_user(
                request,
                "Local node is not registered; remote actions are unavailable.",
                level=messages.ERROR,
            )
            return None, None
        private_key = local.get_private_key()
        if private_key is None:
            self.message_user(
                request,
                "Local node private key is unavailable; remote actions are disabled.",
                level=messages.ERROR,
            )
            return None, None
        return local, private_key

    def _call_remote_action(
        self,
        request,
        local_node: Node,
        private_key,
        charger: Charger,
        action: str,
        extra: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        if not charger.node_origin:
            self.message_user(
                request,
                f"{charger}: remote node information is missing.",
                level=messages.ERROR,
            )
            return False, {}
        origin = charger.node_origin
        if not origin.port:
            self.message_user(
                request,
                f"{charger}: remote node port is not configured.",
                level=messages.ERROR,
            )
            return False, {}

        if not origin.get_remote_host_candidates():
            self.message_user(
                request,
                f"{charger}: remote node connection details are incomplete.",
                level=messages.ERROR,
            )
            return False, {}

        payload: dict[str, Any] = {
            "requester": str(local_node.uuid),
            "requester_mac": local_node.mac_address,
            "requester_public_key": local_node.public_key,
            "charger_id": charger.charger_id,
            "connector_id": charger.connector_id,
            "action": action,
        }
        if extra:
            payload.update(extra)

        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        headers = {"Content-Type": "application/json"}
        try:
            signature = private_key.sign(
                payload_json.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            headers["X-Signature"] = base64.b64encode(signature).decode()
        except Exception:
            self.message_user(
                request,
                "Unable to sign remote action payload; remote action aborted.",
                level=messages.ERROR,
            )
            return False, {}

        url = next(
            origin.iter_remote_urls("/nodes/network/chargers/action/"),
            "",
        )
        if not url:
            self.message_user(
                request,
                f"{charger}: no reachable hosts were reported for the remote node.",
                level=messages.ERROR,
            )
            return False, {}
        try:
            response = requests.post(url, data=payload_json, headers=headers, timeout=5)
        except RequestException as exc:
            self.message_user(
                request,
                f"{charger}: failed to contact remote node ({exc}).",
                level=messages.ERROR,
            )
            return False, {}

        try:
            data = response.json()
        except ValueError:
            self.message_user(
                request,
                f"{charger}: invalid response from remote node.",
                level=messages.ERROR,
            )
            return False, {}

        if response.status_code != 200 or data.get("status") != "ok":
            detail = data.get("detail") if isinstance(data, dict) else None
            if not detail:
                detail = response.text or "Remote node rejected the request."
            self.message_user(
                request,
                f"{charger}: {detail}",
                level=messages.ERROR,
            )
            return False, {}

        updates = data.get("updates", {}) if isinstance(data, dict) else {}
        if not isinstance(updates, dict):
            updates = {}
        return True, updates

    def _apply_remote_updates(self, charger: Charger, updates: dict[str, Any]) -> None:
        if not updates:
            return

        applied: dict[str, Any] = {}
        for field, value in updates.items():
            if field in self._REMOTE_DATETIME_FIELDS and isinstance(value, str):
                parsed = parse_datetime(value)
                if parsed and timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
                applied[field] = parsed
            else:
                applied[field] = value

        Charger.objects.filter(pk=charger.pk).update(**applied)
        for field, value in applied.items():
            setattr(charger, field, value)

    @admin.action(description="Get diagnostics")
    def get_diagnostics(self, request, queryset):
        diagnostics_dir, user_dir = self._diagnostics_directory_for(request.user)
        successes: list[tuple[Charger, str, Path]] = []
        for charger in queryset:
            location = (charger.diagnostics_location or "").strip()
            if not location:
                self.message_user(
                    request,
                    _("%(charger)s: no diagnostics location reported.")
                    % {"charger": charger},
                    level=messages.WARNING,
                )
                continue
            try:
                destination, asset_url = self._download_diagnostics(
                    request,
                    charger,
                    location,
                    diagnostics_dir,
                    user_dir,
                )
            except self.DiagnosticsDownloadError as exc:
                self.message_user(
                    request,
                    _("%(charger)s: %(error)s")
                    % {"charger": charger, "error": exc},
                    level=messages.ERROR,
                )
                continue
            successes.append((charger, asset_url, destination))

        if successes:
            summary = ngettext(
                "Retrieved diagnostics for %(count)d charger.",
                "Retrieved diagnostics for %(count)d chargers.",
                len(successes),
            ) % {"count": len(successes)}
            details = format_html_join(
                "",
                "<li>{}: <a href=\"{}\" target=\"_blank\">{}</a> (<code>{}</code>)</li>",
                (
                    (charger, url, destination.name, destination)
                    for charger, url, destination in successes
                ),
            )
            message = format_html("{}<ul>{}</ul>", summary, details)
            self.message_user(request, message, level=messages.SUCCESS)

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj and not obj.is_local:
            for field in ("allow_remote", "export_transactions"):
                if field not in readonly:
                    readonly.append(field)
        return tuple(readonly)

    def get_view_on_site_url(self, obj=None):
        return obj.get_absolute_url() if obj else None

    def require_rfid_display(self, obj):
        return obj.require_rfid

    require_rfid_display.boolean = True
    require_rfid_display.short_description = "RFID Auth"

    def page_link(self, obj):
        from django.utils.html import format_html

        return format_html(
            '<a href="{}" target="_blank">open</a>', obj.get_absolute_url()
        )

    page_link.short_description = "Landing"

    def qr_link(self, obj):
        from django.utils.html import format_html

        if obj.reference and obj.reference.image:
            return format_html(
                '<a href="{}" target="_blank">qr</a>', obj.reference.image.url
            )
        return ""

    qr_link.short_description = "QR Code"

    def log_link(self, obj):
        from django.utils.html import format_html
        from django.urls import reverse

        url = reverse("admin:ocpp_charger_log", args=[obj.pk])
        return format_html('<a href="{}" target="_blank">view</a>', url)

    log_link.short_description = "Log"

    def get_log_identifier(self, obj):
        return store.identity_key(obj.charger_id, obj.connector_id)

    def connector_number(self, obj):
        return obj.connector_id if obj.connector_id is not None else ""

    connector_number.short_description = "#"
    connector_number.admin_order_field = "connector_id"

    def status_link(self, obj):
        from django.utils.html import format_html
        from django.urls import reverse

        url = reverse(
            "charger-status-connector",
            args=[obj.charger_id, obj.connector_slug],
        )
        tx_obj = store.get_transaction(obj.charger_id, obj.connector_id)
        state, _ = _charger_state(
            obj,
            tx_obj
            if obj.connector_id is not None
            else (_live_sessions(obj) or None),
        )
        return format_html('<a href="{}" target="_blank">{}</a>', url, state)

    status_link.short_description = "Status"

    def _has_active_session(self, charger: Charger) -> bool:
        """Return whether ``charger`` currently has an active session."""

        if store.get_transaction(charger.charger_id, charger.connector_id):
            return True
        if charger.connector_id is not None:
            return False
        sibling_connectors = (
            Charger.objects.filter(charger_id=charger.charger_id)
            .exclude(pk=charger.pk)
            .values_list("connector_id", flat=True)
        )
        for connector_id in sibling_connectors:
            if store.get_transaction(charger.charger_id, connector_id):
                return True
        return False

    @admin.display(description="Display Name", ordering="display_name")
    def display_name_with_fallback(self, obj):
        return self._charger_display_name(obj)

    @admin.display(description="Charger", ordering="display_name")
    def charger_name_display(self, obj):
        return self._charger_display_name(obj)

    def _charger_display_name(self, obj):
        if obj.display_name:
            return obj.display_name
        if obj.location:
            return obj.location.name
        return obj.charger_id

    def _build_local_authorization_list(self) -> list[dict[str, object]]:
        """Return the payload for SendLocalList with released and allowed RFIDs."""

        entries: list[dict[str, object]] = []
        queryset = (
            CoreRFID.objects.filter(released=True, allowed=True)
            .order_by("rfid")
            .only("rfid")
        )
        for tag in queryset.iterator():
            entry: dict[str, object] = {"idTag": tag.rfid}
            entry["idTagInfo"] = {"status": "Accepted"}
            entries.append(entry)
        return entries

    @admin.display(boolean=True, description="Local")
    def local_indicator(self, obj):
        return obj.is_local

    def location_name(self, obj):
        return obj.location.name if obj.location else ""

    location_name.short_description = "Location"

    def purge_data(self, request, queryset):
        for charger in queryset:
            charger.purge()
        self.message_user(request, "Data purged for selected chargers")

    purge_data.short_description = "Purge data"

    @admin.action(description="Re-check Charger Status")
    def recheck_charger_status(self, request, queryset):
        requested = 0
        for charger in queryset:
            connector_value = charger.connector_id
            ws = store.get_connection(charger.charger_id, connector_value)
            if ws is None:
                self.message_user(
                    request,
                    f"{charger}: no active connection",
                    level=messages.ERROR,
                )
                continue
            payload: dict[str, object] = {"requestedMessage": "StatusNotification"}
            trigger_connector: int | None = None
            if connector_value is not None:
                payload["connectorId"] = connector_value
                trigger_connector = connector_value
            message_id = uuid.uuid4().hex
            msg = json.dumps([2, message_id, "TriggerMessage", payload])
            try:
                async_to_sync(ws.send)(msg)
            except Exception as exc:  # pragma: no cover - network error
                self.message_user(
                    request,
                    f"{charger}: failed to send TriggerMessage ({exc})",
                    level=messages.ERROR,
                )
                continue
            log_key = store.identity_key(charger.charger_id, connector_value)
            store.add_log(log_key, f"< {msg}", log_type="charger")
            store.register_pending_call(
                message_id,
                {
                    "action": "TriggerMessage",
                    "charger_id": charger.charger_id,
                    "connector_id": connector_value,
                    "log_key": log_key,
                    "trigger_target": "StatusNotification",
                    "trigger_connector": trigger_connector,
                    "requested_at": timezone.now(),
                },
            )
            store.schedule_call_timeout(
                message_id,
                timeout=5.0,
                action="TriggerMessage",
                log_key=log_key,
                message="TriggerMessage StatusNotification timed out",
            )
            requested += 1
        if requested:
            self.message_user(
                request,
                f"Requested status update from {requested} charger(s)",
            )

    @admin.action(description="Fetch CP configuration")
    def fetch_cp_configuration(self, request, queryset):
        fetched = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                payload = {}
                msg = json.dumps([2, message_id, "GetConfiguration", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send GetConfiguration ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                store.register_pending_call(
                    message_id,
                    {
                        "action": "GetConfiguration",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "requested_at": timezone.now(),
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    timeout=5.0,
                    action="GetConfiguration",
                    log_key=log_key,
                    message=(
                        "GetConfiguration timed out: charger did not respond"
                        " (operation may not be supported)"
                    ),
                )
                fetched += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "get-configuration",
            )
            if success:
                self._apply_remote_updates(charger, updates)
                fetched += 1

        if fetched:
            self.message_user(
                request,
                f"Requested configuration from {fetched} charger(s)",
            )

    @admin.action(description="Toggle RFID Authentication")
    def toggle_rfid_authentication(self, request, queryset):
        enabled = 0
        disabled = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            new_value = not charger.require_rfid
            if charger.is_local:
                Charger.objects.filter(pk=charger.pk).update(require_rfid=new_value)
                charger.require_rfid = new_value
                if new_value:
                    enabled += 1
                else:
                    disabled += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "toggle-rfid",
                {"enable": new_value},
            )
            if success:
                self._apply_remote_updates(charger, updates)
                if charger.require_rfid:
                    enabled += 1
                else:
                    disabled += 1

        if enabled or disabled:
            changes = []
            if enabled:
                changes.append(f"enabled for {enabled} charger(s)")
            if disabled:
                changes.append(f"disabled for {disabled} charger(s)")
            summary = "; ".join(changes)
            self.message_user(
                request,
                f"Updated RFID authentication: {summary}",
            )

    @admin.action(description="Send RFID list to EVCS")
    def send_rfid_list_to_evcs(self, request, queryset):
        authorization_list = self._build_local_authorization_list()
        update_type = "Full"
        sent = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            list_version = (charger.local_auth_list_version or 0) + 1
            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                payload = {
                    "listVersion": list_version,
                    "updateType": update_type,
                    "localAuthorizationList": authorization_list,
                }
                msg = json.dumps([2, message_id, "SendLocalList", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send SendLocalList ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                store.register_pending_call(
                    message_id,
                    {
                        "action": "SendLocalList",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "list_version": list_version,
                        "list_size": len(authorization_list),
                        "requested_at": timezone.now(),
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    action="SendLocalList",
                    log_key=log_key,
                    message="SendLocalList request timed out",
                )
                sent += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            extra = {
                "local_authorization_list": [entry.copy() for entry in authorization_list],
                "list_version": list_version,
                "update_type": update_type,
            }
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "send-local-rfid-list",
                extra,
            )
            if success:
                self._apply_remote_updates(charger, updates)
                sent += 1

        if sent:
            self.message_user(
                request,
                f"Sent SendLocalList to {sent} charger(s)",
            )

    @admin.action(description="Update RFIDs from EVCS")
    def update_rfids_from_evcs(self, request, queryset):
        requested = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                payload: dict[str, object] = {}
                msg = json.dumps([2, message_id, "GetLocalListVersion", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send GetLocalListVersion ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                store.register_pending_call(
                    message_id,
                    {
                        "action": "GetLocalListVersion",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "requested_at": timezone.now(),
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    action="GetLocalListVersion",
                    log_key=log_key,
                    message="GetLocalListVersion request timed out",
                )
                requested += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "get-local-list-version",
            )
            if success:
                self._apply_remote_updates(charger, updates)
                requested += 1

        if requested:
            self.message_user(
                request,
                f"Requested GetLocalListVersion from {requested} charger(s)",
            )

    def _dispatch_change_availability(self, request, queryset, availability_type: str):
        sent = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                connector_id = connector_value if connector_value is not None else 0
                message_id = uuid.uuid4().hex
                payload = {"connectorId": connector_id, "type": availability_type}
                msg = json.dumps([2, message_id, "ChangeAvailability", payload])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send ChangeAvailability ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                timestamp = timezone.now()
                store.register_pending_call(
                    message_id,
                    {
                        "action": "ChangeAvailability",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "availability_type": availability_type,
                        "requested_at": timestamp,
                    },
                )
                updates = {
                    "availability_requested_state": availability_type,
                    "availability_requested_at": timestamp,
                    "availability_request_status": "",
                    "availability_request_status_at": None,
                    "availability_request_details": "",
                }
                Charger.objects.filter(pk=charger.pk).update(**updates)
                for field, value in updates.items():
                    setattr(charger, field, value)
                sent += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "change-availability",
                {"availability_type": availability_type},
            )
            if success:
                self._apply_remote_updates(charger, updates)
                sent += 1

        if sent:
            self.message_user(
                request,
                f"Sent ChangeAvailability ({availability_type}) to {sent} charger(s)",
            )

    @admin.action(description="Set availability to Operative")
    def change_availability_operative(self, request, queryset):
        self._dispatch_change_availability(request, queryset, "Operative")

    @admin.action(description="Set availability to Inoperative")
    def change_availability_inoperative(self, request, queryset):
        self._dispatch_change_availability(request, queryset, "Inoperative")

    def _set_availability_state(
        self, request, queryset, availability_state: str
    ) -> None:
        updated = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                timestamp = timezone.now()
                updates = {
                    "availability_state": availability_state,
                    "availability_state_updated_at": timestamp,
                }
                Charger.objects.filter(pk=charger.pk).update(**updates)
                for field, value in updates.items():
                    setattr(charger, field, value)
                updated += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "set-availability-state",
                {"availability_state": availability_state},
            )
            if success:
                self._apply_remote_updates(charger, updates)
                updated += 1

        if updated:
            self.message_user(
                request,
                f"Updated availability to {availability_state} for {updated} charger(s)",
            )

    @admin.action(description="Mark availability as Operative")
    def set_availability_state_operative(self, request, queryset):
        self._set_availability_state(request, queryset, "Operative")

    @admin.action(description="Mark availability as Inoperative")
    def set_availability_state_inoperative(self, request, queryset):
        self._set_availability_state(request, queryset, "Inoperative")

    @admin.action(description="Clear charger authorization cache")
    def clear_authorization_cache(self, request, queryset):
        cleared = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                msg = json.dumps([2, message_id, "ClearCache", {}])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send ClearCache ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                requested_at = timezone.now()
                store.register_pending_call(
                    message_id,
                    {
                        "action": "ClearCache",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "requested_at": requested_at,
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    action="ClearCache",
                    log_key=log_key,
                )
                cleared += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, _updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "clear-cache",
            )
            if success:
                cleared += 1

        if cleared:
            self.message_user(
                request,
                f"Sent ClearCache to {cleared} charger(s)",
            )

    @admin.action(description="Remote stop active transaction")
    def remote_stop_transaction(self, request, queryset):
        stopped = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                tx_obj = store.get_transaction(charger.charger_id, connector_value)
                if tx_obj is None:
                    self.message_user(
                        request,
                        f"{charger}: no active transaction",
                        level=messages.ERROR,
                    )
                    continue
                message_id = uuid.uuid4().hex
                payload = {"transactionId": tx_obj.pk}
                msg = json.dumps([
                    2,
                    message_id,
                    "RemoteStopTransaction",
                    payload,
                ])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send RemoteStopTransaction ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                store.register_pending_call(
                    message_id,
                    {
                        "action": "RemoteStopTransaction",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "transaction_id": tx_obj.pk,
                        "log_key": log_key,
                        "requested_at": timezone.now(),
                    },
                )
                stopped += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "remote-stop",
            )
            if success:
                self._apply_remote_updates(charger, updates)
                stopped += 1

        if stopped:
            self.message_user(
                request,
                f"Sent RemoteStopTransaction to {stopped} charger(s)",
            )

    @admin.action(description="Reset charger (soft)")
    def reset_chargers(self, request, queryset):
        reset = 0
        local_node = None
        private_key = None
        remote_unavailable = False
        for charger in queryset:
            if charger.is_local:
                connector_value = charger.connector_id
                ws = store.get_connection(charger.charger_id, connector_value)
                if ws is None:
                    self.message_user(
                        request,
                        f"{charger}: no active connection",
                        level=messages.ERROR,
                    )
                    continue
                tx_obj = store.get_transaction(charger.charger_id, connector_value)
                if tx_obj is not None:
                    self.message_user(
                        request,
                        (
                            f"{charger}: reset skipped because a session is active; "
                            "stop the session first."
                        ),
                        level=messages.WARNING,
                    )
                    continue
                message_id = uuid.uuid4().hex
                msg = json.dumps([
                    2,
                    message_id,
                    "Reset",
                    {"type": "Soft"},
                ])
                try:
                    async_to_sync(ws.send)(msg)
                except Exception as exc:  # pragma: no cover - network error
                    self.message_user(
                        request,
                        f"{charger}: failed to send Reset ({exc})",
                        level=messages.ERROR,
                    )
                    continue
                log_key = store.identity_key(charger.charger_id, connector_value)
                store.add_log(log_key, f"< {msg}", log_type="charger")
                store.register_pending_call(
                    message_id,
                    {
                        "action": "Reset",
                        "charger_id": charger.charger_id,
                        "connector_id": connector_value,
                        "log_key": log_key,
                        "requested_at": timezone.now(),
                    },
                )
                store.schedule_call_timeout(
                    message_id,
                    timeout=5.0,
                    action="Reset",
                    log_key=log_key,
                    message="Reset timed out: charger did not respond",
                )
                reset += 1
                continue

            if not charger.allow_remote:
                self.message_user(
                    request,
                    f"{charger}: remote administration is disabled.",
                    level=messages.ERROR,
                )
                continue
            if remote_unavailable:
                continue
            if local_node is None:
                local_node, private_key = self._prepare_remote_credentials(request)
                if not local_node or not private_key:
                    remote_unavailable = True
                    continue
            success, updates = self._call_remote_action(
                request,
                local_node,
                private_key,
                charger,
                "reset",
                {"reset_type": "Soft"},
            )
            if success:
                self._apply_remote_updates(charger, updates)
                reset += 1

        if reset:
            self.message_user(
                request,
                f"Sent Reset to {reset} charger(s)",
            )

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            obj.delete()

    def total_kw_display(self, obj):
        return round(obj.total_kw, 2)

    total_kw_display.short_description = "Total kW"

    def today_kw(self, obj):
        start, end = self._today_range()
        return round(obj.total_kw_for_range(start, end), 2)

    today_kw.short_description = "Today kW"

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context=extra_context)
        if hasattr(response, "context_data"):
            cl = response.context_data.get("cl")
            if cl is not None:
                response.context_data.update(
                    self._charger_quick_stats_context(cl.queryset)
                )
        return response

    def _charger_quick_stats_context(self, queryset):
        chargers = list(queryset)
        stats = {"total_kw": 0.0, "today_kw": 0.0}
        if not chargers:
            return {"charger_quick_stats": stats}

        parent_ids = {c.charger_id for c in chargers if c.connector_id is None}
        start, end = self._today_range()

        for charger in chargers:
            include_totals = True
            if charger.connector_id is not None and charger.charger_id in parent_ids:
                include_totals = False
            if include_totals:
                stats["total_kw"] += charger.total_kw
                stats["today_kw"] += charger.total_kw_for_range(start, end)

        stats = {key: round(value, 2) for key, value in stats.items()}
        return {"charger_quick_stats": stats}

    def _today_range(self):
        today = timezone.localdate()
        start = datetime.combine(today, time.min)
        if timezone.is_naive(start):
            start = timezone.make_aware(start, timezone.get_current_timezone())
        end = start + timedelta(days=1)
        return start, end


@admin.register(Simulator)
class SimulatorAdmin(SaveBeforeChangeAction, LogViewAdminMixin, EntityModelAdmin):
    list_display = (
        "name",
        "cp_path",
        "host",
        "ws_port",
        "ws_url",
        "interval",
        "kw_max_display",
        "running",
        "log_link",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "cp_path",
                    ("host", "ws_port"),
                    "rfid",
                    ("duration", "interval", "pre_charge_delay"),
                    "kw_max",
                    ("repeat", "door_open"),
                    ("username", "password"),
                )
            },
        ),
        (
            "Configuration",
            {
                "fields": (
                    "configuration_keys",
                    "configuration_unknown_keys",
                ),
                "classes": ("collapse",),
                "description": (
                    "Provide JSON lists for configurationKey entries and "
                    "unknownKey values returned by GetConfiguration."
                ),
            },
        ),
    )
    actions = ("start_simulator", "stop_simulator", "send_open_door")
    change_actions = ["start_simulator_action", "stop_simulator_action"]

    log_type = "simulator"

    @admin.display(description="kW Max", ordering="kw_max")
    def kw_max_display(self, obj):
        """Display ``kw_max`` with a dot decimal separator for Spanish locales."""

        language = translation.get_language() or ""
        if language.startswith("es"):
            return formats.number_format(
                obj.kw_max,
                decimal_pos=2,
                use_l10n=False,
                force_grouping=False,
            )

        return formats.number_format(
            obj.kw_max,
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

    def start_simulator(self, request, queryset):
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

    start_simulator.short_description = "Start selected simulators"

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

from ..common_imports import *
from ...models import (
    CustomerInformationRequest,
    CustomerInformationChunk,
    DisplayMessageNotification,
    DisplayMessage,
)

import logging

logger = logging.getLogger(__name__)


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
            logger.exception(
                "Failed to send ChangeConfiguration for charger %s with payload %r",
                charger,
                payload,
            )
            message = _("Failed to send ChangeConfiguration.")
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
            message = _("ChangeConfiguration failed: %(details)s") % {
                "details": description
            }
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
            message = _("Only local charge points can be restarted from this server.")
            return False, message

        connector_value = charger.connector_id
        ws = store.get_connection(charger.charger_id, connector_value)
        if ws is None:
            message = _("%(charger)s is not connected to the platform.") % {
                "charger": charger,
            }
            # Log the full exception server-side, but return a generic error message to the client.
            logging.exception("Failed to send Reset to charger %s (connector %s)", charger.charger_id, connector_value)
            message = _("Failed to send Reset due to an internal error.")

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
            return False, _("Reset did not receive a response from the charger.")
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
                    _("%(identifier)s has no associated charger to refresh.")
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
    forwarded_calls = forms.MultipleChoiceField(
        label=_("Forwarded calls"),
        choices=[
            (action, action)
            for action in CPForwarder.available_forwarded_calls()
        ],
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text=_(
            "Choose which CSMS actions should be accepted from the remote node."
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
        call_initial = CPForwarder.available_forwarded_calls()
        if self.instance and self.instance.pk:
            call_initial = self.instance.get_forwarded_calls()
        self.fields["forwarded_calls"].initial = call_initial

    def clean_forwarded_messages(self):
        selected = self.cleaned_data.get("forwarded_messages") or []
        return CPForwarder.sanitize_forwarded_messages(selected)

    def clean_forwarded_calls(self):
        selected = self.cleaned_data.get("forwarded_calls") or []
        return CPForwarder.sanitize_forwarded_calls(selected)


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
                "fields": ("forwarded_messages", "forwarded_calls"),
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
    change_list_template = "admin/ocpp/stationmodel/change_list.html"
    list_display = (
        "vendor",
        "model_family",
        "model",
        "preferred_ocpp_version",
        "integration_rating",
        "max_power_kw",
        "max_voltage_v",
    )
    search_fields = ("vendor", "model_family", "model")
    list_filter = ("preferred_ocpp_version", "integration_rating")
    raw_id_fields = ("images_bucket", "documents_bucket")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "view-in-site/",
                self.admin_site.admin_view(self.view_in_site),
                name="ocpp_stationmodel_view_in_site",
            )
        ]
        return custom + urls

    def view_in_site(self, request):
        return HttpResponseRedirect(reverse("ocpp:supported-chargers"))

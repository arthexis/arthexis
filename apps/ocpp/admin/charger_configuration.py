from django.utils.translation import gettext_lazy as _

from .common_imports import *

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

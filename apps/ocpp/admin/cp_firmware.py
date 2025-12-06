from django.utils.translation import gettext_lazy as _

from .common_imports import *

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

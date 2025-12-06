from .common_imports import *

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

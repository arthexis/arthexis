from .common_imports import *

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

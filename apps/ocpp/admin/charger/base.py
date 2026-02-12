"""Base charger admin wiring and common presentation helpers."""

from ..common_imports import *
from ..common import LogViewAdminMixin
from .utils import _charger_state, _live_sessions, store


class ChargerAdminBaseMixin(LogViewAdminMixin, OwnableAdminMixin, EntityModelAdmin):
    """Core model admin configuration and common helper methods."""

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
                    "preferred_ocpp_version",
                    "energy_unit",
                    "location",
                    "station_model",
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
                    "diagnostics_bucket",
                )
            },
        ),
        (
            "Maintenance",
            {
                "fields": (
                    "maintenance_email",
                    "email_when_offline",
                    "offline_notification_sent_at",
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
            {
                "fields": (
                    "public_display",
                    "require_rfid",
                    "configuration",
                    "network_profile",
                )
            },
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
                "description": _(
                    "Only charge points with Export transactions enabled can be "
                    "forwarded. Allow remote lets the manager or forwarder send "
                    "commands to the device."
                ),
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
            "Authentication",
            {
                "description": _(
                    "Configure HTTP Basic authentication requirements for this charge point."
                ),
                "fields": ("ws_auth_user", "ws_auth_group"),
            },
        ),
        (
            "References",
            {
                "fields": ("reference",),
            },
        ),
        (
            "Visibility",
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
        "diagnostics_bucket",
        "forwarded_to",
        "forwarding_watermark",
        "last_online_at",
        "offline_notification_sent_at",
    )
    list_display = (
        "display_name_with_fallback",
        "connector_number",
        "local_indicator",
        "require_rfid_display",
        "public_display",
        "forwarding_ready",
        "last_heartbeat_display",
        "today_kw",
        "total_kw_display",
        "page_link",
        "log_link",
        "status_link",
    )
    list_filter = ("export_transactions",)
    search_fields = ("charger_id", "connector_id", "location__name")
    filter_horizontal = ("owner_users", "owner_groups")

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

    require_rfid_display.short_description = "RF Auth"

    @admin.display(boolean=True, description="Fwd OK")
    def forwarding_ready(self, obj):
        return bool(obj.forwarded_to_id and obj.export_transactions)

    @admin.display(description="Last heartbeat", ordering="last_heartbeat")
    def last_heartbeat_display(self, obj):
        value = obj.last_heartbeat
        if not value:
            return "-"
        if timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_current_timezone())
        localized = timezone.localtime(value)
        iso_value = localized.isoformat(timespec="minutes")
        return iso_value.replace("T", " ")

    def page_link(self, obj):
        from django.utils.html import format_html

        return format_html(
            '<a href="{}" target="_blank">open</a>', obj.get_absolute_url()
        )

    page_link.short_description = "Landing"

    def qr_link(self, obj):
        from django.utils.html import format_html

        if obj.reference and obj.reference.image_url:
            return format_html(
                '<a href="{}" target="_blank">qr</a>', obj.reference.image_url
            )
        return ""

    qr_link.short_description = "QR Code"

    def log_link(self, obj):
        from django.utils.html import format_html

        info = self.model._meta.app_label, self.model._meta.model_name
        url = reverse(
            "admin:%s_%s_log" % info,
            args=[quote(obj.pk)],
            current_app=self.admin_site.name,
        )
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
            "ocpp:charger-status-connector",
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

    def _charger_display_name(self, obj):
        if obj.display_name:
            return obj.display_name
        if obj.location:
            return obj.location.name
        return obj.charger_id

    @admin.display(boolean=True, description="Local")
    def local_indicator(self, obj):
        return obj.is_local

    def location_name(self, obj):
        return obj.location.name if obj.location else ""

    location_name.short_description = "Location"

    def _build_purge_summaries(self, queryset):
        target_chargers: dict[int, Charger] = {}

        for charger in queryset:
            for target in charger._target_chargers():
                target_chargers[target.pk] = target

        summaries: dict[str, dict[str, object]] = {}
        for target in target_chargers.values():
            key = target.charger_id
            summary = summaries.get(key)
            if summary is None:
                summary = {
                    "charger": target,
                    "display_name": self._charger_display_name(target),
                    "transactions": 0,
                    "meter_values": 0,
                }
                summaries[key] = summary
            elif summary["charger"].connector_id is not None and target.connector_id is None:
                summary["charger"] = target
                summary["display_name"] = self._charger_display_name(target)

            summary["transactions"] += target.transactions.count()
            summary["meter_values"] += target.meter_values.count()

        for summary in summaries.values():
            summary["total_rows"] = summary["transactions"] + summary["meter_values"]

        return sorted(
            summaries.values(), key=lambda item: item["display_name"].lower()
        )

    @admin.action(description=_("Clear all selected CP data"))
    def purge_data(self, request, queryset):
        purge_summaries = self._build_purge_summaries(queryset)

        for charger in queryset:
            charger.purge()

        total_rows = sum(summary["total_rows"] for summary in purge_summaries)
        self.message_user(
            request,
            _("Purged %(rows)s rows across %(count)s charge points.")
            % {"rows": total_rows, "count": len(purge_summaries)},
        )
        return None

    def delete_queryset(self, request, queryset):
        protected: list[Charger] = []
        for obj in queryset:
            try:
                obj.delete()
            except ProtectedError:
                protected.append(obj)
        if protected:
            count = len(protected)
            message = ngettext(
                "Purge charger data before deleting this charger.",
                "Purge charger data before deleting these chargers.",
                count,
            )
            self.message_user(request, message, level=messages.ERROR)

    def delete_view(self, request, object_id, extra_context=None):
        try:
            return super().delete_view(
                request, object_id, extra_context=extra_context
            )
        except ProtectedError:
            if request.method == "POST":
                self.message_user(
                    request,
                    _("Purge charger data before deleting this charger."),
                    level=messages.ERROR,
                )
                change_url = reverse("admin:ocpp_charger_change", args=[object_id])
                return HttpResponseRedirect(change_url)
            raise

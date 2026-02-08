from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib import admin, messages
from django.contrib.admin.utils import quote
from django.db.models.deletion import ProtectedError
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _, ngettext

from apps.core.admin import OwnableAdminMixin
from apps.energy.models import EnergyTariff
from apps.locals.user_data import EntityModelAdmin

from ... import store
from ...models import Charger
from ...status_resets import clear_stale_cached_statuses
from .actions import ChargerAdminActionsMixin
from .helpers import charger_status_state
from ..miscellaneous.simulator_admin import LogViewAdminMixin
from .views import ChargerAdminViewsMixin


@admin.register(Charger)
class ChargerAdmin(
    ChargerAdminActionsMixin,
    ChargerAdminViewsMixin,
    LogViewAdminMixin,
    OwnableAdminMixin,
    EntityModelAdmin,
):
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
                    "ftp_server",
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
                    "configuration_check_enabled",
                    "power_projection_enabled",
                    "firmware_snapshot_enabled",
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
    actions = [
        "purge_data",
        "fetch_cp_configuration",
        "toggle_rfid_authentication",
        "send_rfid_list_to_evcs",
        "update_rfids_from_evcs",
        "recheck_charger_status",
        "setup_cp_diagnostics",
        "configure_local_ftp_server",
        "request_cp_diagnostics",
        "get_diagnostics",
        "change_availability_operative",
        "change_availability_inoperative",
        "set_availability_state_operative",
        "set_availability_state_inoperative",
        "clear_authorization_cache",
        "clear_charging_profiles",
        "remote_stop_transaction",
        "reset_chargers",
        "create_simulator_for_cp",
        "setup_charger_location",
        "view_charge_point_dashboard",
        "delete_selected",
    ]

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
        return format_html(
            '<a href="{}" target="_blank">open</a>', obj.get_absolute_url()
        )

    page_link.short_description = "Landing"

    def qr_link(self, obj):
        if obj.reference and obj.reference.image_url:
            return format_html(
                '<a href="{}" target="_blank">qr</a>', obj.reference.image_url
            )
        return ""

    qr_link.short_description = "QR Code"

    def log_link(self, obj):
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
        url = reverse(
            "ocpp:charger-status-connector",
            args=[obj.charger_id, obj.connector_slug],
        )
        state = charger_status_state(obj)
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

    def total_kw_display(self, obj):
        return round(obj.total_kw, 2)

    total_kw_display.short_description = "Total kW"

    def today_kw(self, obj):
        start, end = self._today_range()
        return round(obj.total_kw_for_range(start, end), 2)

    today_kw.short_description = "Today kW"

    def changelist_view(self, request, extra_context=None):
        clear_stale_cached_statuses()
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
        stats = {
            "total_kw": 0.0,
            "today_kw": 0.0,
            "estimated_cost": None,
            "availability_percentage": None,
        }
        if not chargers:
            return {"charger_quick_stats": stats}

        parent_ids = {c.charger_id for c in chargers if c.connector_id is None}
        start, end = self._today_range()
        window_end = timezone.now()
        window_start = window_end - timedelta(hours=24)
        tariff_cache = self._build_tariff_cache(window_end)
        estimated_cost = Decimal("0")
        cost_available = False
        reported_count = 0
        available_count = 0

        for charger in chargers:
            include_totals = True
            if charger.connector_id is not None and charger.charger_id in parent_ids:
                include_totals = False
            if not include_totals:
                continue

            stats["total_kw"] += charger.total_kw
            stats["today_kw"] += charger.total_kw_for_range(start, end)

            energy_window = Decimal(
                str(charger.total_kw_for_range(window_start, window_end))
            )
            price = self._select_tariff_price(
                tariff_cache,
                getattr(charger.location, "zone", None),
                getattr(charger.location, "contract_type", None),
                window_end,
            )
            if price is not None:
                estimated_cost += energy_window * price
                cost_available = True

            availability_state = self._charger_availability_state(charger)
            availability_timestamp = self._charger_availability_timestamp(charger)
            if availability_timestamp and availability_timestamp >= window_start:
                reported_count += 1
                if availability_state.casefold() == "operative":
                    available_count += 1

        stats["total_kw"] = round(stats["total_kw"], 2)
        stats["today_kw"] = round(stats["today_kw"], 2)
        if cost_available:
            stats["estimated_cost"] = estimated_cost.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        if reported_count:
            stats["availability_percentage"] = round(
                (available_count / reported_count) * 100.0, 1
            )

        return {"charger_quick_stats": stats}

    @staticmethod
    def _tariff_active_at(tariff, moment: time) -> bool:
        start = tariff.start_time
        end = tariff.end_time
        if start <= end:
            return start <= moment < end
        return moment >= start or moment < end

    def _build_tariff_cache(self, reference_time: datetime) -> dict[tuple[str | None, str | None], list[EnergyTariff]]:
        tariffs = list(
            EnergyTariff.objects.filter(
                unit=EnergyTariff.Unit.KWH, year__lte=reference_time.year
            ).order_by("-year", "season", "start_time")
        )
        cache: dict[tuple[str | None, str | None], list[EnergyTariff]] = {}
        fallback: list[EnergyTariff] = []
        for tariff in tariffs:
            key = (tariff.zone, tariff.contract_type)
            cache.setdefault(key, []).append(tariff)
            fallback.append(tariff)
        cache[(None, None)] = fallback
        return cache

    def _select_tariff_price(
        self,
        cache: dict[tuple[str | None, str | None], list[EnergyTariff]],
        zone: str | None,
        contract_type: str | None,
        reference_time: datetime,
    ) -> Decimal | None:
        key = (zone or None, contract_type or None)
        candidates = cache.get(key)
        if not candidates:
            candidates = cache.get((None, None), [])
        if not candidates:
            return None
        moment = reference_time.time()
        for tariff in candidates:
            if self._tariff_active_at(tariff, moment):
                return tariff.price_mxn
        return candidates[0].price_mxn

    @staticmethod
    def _charger_availability_state(charger) -> str:
        state = (getattr(charger, "availability_state", "") or "").strip()
        if state:
            return state
        derived = Charger.availability_state_from_status(
            getattr(charger, "last_status", "")
        )
        return derived or ""

    @staticmethod
    def _charger_availability_timestamp(charger):
        timestamp = getattr(charger, "availability_state_updated_at", None)
        if timestamp:
            return timestamp
        return getattr(charger, "last_status_timestamp", None)

    def _today_range(self):
        today = timezone.localdate()
        start = datetime.combine(today, time.min)
        if timezone.is_naive(start):
            start = timezone.make_aware(start, timezone.get_current_timezone())
        end = start + timedelta(days=1)
        return start, end

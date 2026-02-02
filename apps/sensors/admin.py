from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib import admin, messages
from django.db.models import Avg
from django.db.models.functions import TruncDate, TruncHour
from django.http import JsonResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Thermometer, ThermometerReading, UsbTracker
from .thermometers import read_w1_temperature


@admin.register(Thermometer)
class ThermometerAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "sampling_interval_seconds",
        "last_reading",
        "last_read_at",
        "is_active",
    )
    search_fields = ("name", "slug")
    list_filter = ("is_active",)
    actions = ("sample_selected_thermometers",)
    change_list_template = "admin/sensors/thermometer/change_list.html"

    _range_options = {
        "month": {"days": 30, "label": _("Last 30 days")},
        "week": {"days": 7, "label": _("Last 7 days")},
        "day": {"days": 1, "label": _("Last 24 hours")},
    }

    @admin.action(description="Sample selected thermometers")
    def sample_selected_thermometers(self, request, queryset):
        updated_count = 0
        failed_names = []
        for thermometer in queryset:
            device_path = f"/sys/bus/w1/devices/{thermometer.slug}/temperature"
            reading = read_w1_temperature(paths=[device_path])
            if reading is None:
                failed_names.append(thermometer.name)
                continue
            thermometer.record_reading(reading, read_at=timezone.now())
            updated_count += 1
        if updated_count:
            self.message_user(
                request,
                f"Sampled {updated_count} thermometer(s).",
                level=messages.SUCCESS,
            )
        if failed_names:
            self.message_user(
                request,
                "Failed to sample the following thermometers: "
                f"{', '.join(failed_names)}.",
                level=messages.WARNING,
            )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "trends/",
                self.admin_site.admin_view(self.trend_view),
                name="sensors_thermometer_trends",
            ),
            path(
                "trend-data/",
                self.admin_site.admin_view(self.trend_data_view),
                name="sensors_thermometer_trend_data",
            ),
        ]
        return custom + urls

    def trend_view(self, request):
        range_key, _ = self._resolve_range(request)
        chart_endpoint = reverse("admin:sensors_thermometer_trend_data")
        if range_key:
            chart_endpoint = f"{chart_endpoint}?range={range_key}"
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Thermometer trends"),
            "chart_endpoint": chart_endpoint,
            "range_key": range_key,
            "range_options": self._range_options,
        }
        return TemplateResponse(
            request,
            "admin/sensors/thermometer/thermometer_trends.html",
            context,
        )

    def trend_data_view(self, request):
        range_key, days = self._resolve_range(request)
        return JsonResponse(self._build_trend_data(days=days, range_key=range_key))

    def _resolve_range(self, request, default: str = "month") -> tuple[str, int]:
        range_key = str(request.GET.get("range") or default).lower()
        if range_key not in self._range_options:
            range_key = default
        return range_key, int(self._range_options[range_key]["days"])

    def _build_trend_data(self, days: int, range_key: str) -> dict:
        end_date = timezone.localdate()
        start_date = end_date - timedelta(days=days - 1)

        start_at = datetime.combine(start_date, time.min)
        end_at = datetime.combine(end_date + timedelta(days=1), time.min)

        if settings.USE_TZ:
            current_tz = timezone.get_current_timezone()
            start_at = timezone.make_aware(start_at, current_tz)
            end_at = timezone.make_aware(end_at, current_tz)
            trunc_expression = (
                TruncHour("read_at", tzinfo=current_tz)
                if days == 1
                else TruncDate("read_at", tzinfo=current_tz)
            )
        else:
            trunc_expression = (
                TruncHour("read_at") if days == 1 else TruncDate("read_at")
            )

        readings = ThermometerReading.objects.filter(
            read_at__gte=start_at, read_at__lt=end_at
        )

        meta = {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "range": range_key,
        }

        if days == 1:
            labels = [
                (start_at + timedelta(hours=offset)).isoformat()
                for offset in range(24)
            ]
        else:
            labels = [
                (start_date + timedelta(days=offset)).isoformat()
                for offset in range(days)
            ]

        if not readings.exists():
            return {"labels": labels, "datasets": [], "meta": meta}

        aggregates = (
            readings.annotate(bucket=trunc_expression)
            .values("bucket", "thermometer_id")
            .annotate(avg_reading=Avg("reading"))
        )

        thermometer_ids = {row["thermometer_id"] for row in aggregates}
        thermometers = list(
            Thermometer.objects.filter(pk__in=thermometer_ids).order_by("name")
        )

        values: dict[int, dict[str, float]] = {
            thermometer.pk: {} for thermometer in thermometers
        }
        for row in aggregates:
            bucket = row["bucket"]
            if bucket is None:
                continue
            label_key = bucket.isoformat()
            avg_value = row["avg_reading"]
            if avg_value is None:
                continue
            values.setdefault(row["thermometer_id"], {})[label_key] = float(avg_value)

        palette = [
            "#1f77b4",
            "#ff7f0e",
            "#2ca02c",
            "#d62728",
            "#9467bd",
            "#8c564b",
            "#e377c2",
            "#7f7f7f",
            "#bcbd22",
            "#17becf",
        ]
        datasets = []
        for index, thermometer in enumerate(thermometers):
            color = palette[index % len(palette)]
            value_map = values.get(thermometer.pk, {})
            datasets.append(
                {
                    "label": thermometer.name,
                    "data": [value_map.get(label) for label in labels],
                    "borderColor": color,
                    "backgroundColor": color,
                    "fill": False,
                    "tension": 0.3,
                }
            )

        return {"labels": labels, "datasets": datasets, "meta": meta}


@admin.register(UsbTracker)
class UsbTrackerAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "required_file_path",
        "recipe",
        "cooldown_seconds",
        "last_triggered_at",
        "is_active",
    )
    search_fields = ("name", "slug", "required_file_path")
    list_filter = ("is_active",)
    readonly_fields = (
        "last_checked_at",
        "last_matched_at",
        "last_triggered_at",
        "last_match_path",
        "last_match_signature",
        "last_recipe_result",
        "last_error",
    )

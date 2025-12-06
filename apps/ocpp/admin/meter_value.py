from .common_imports import *

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

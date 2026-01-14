from django.contrib import admin, messages
from django.utils import timezone

from .models import Thermometer
from .thermometers import read_w1_temperature


@admin.register(Thermometer)
class ThermometerAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "last_reading", "last_read_at", "is_active")
    search_fields = ("name", "slug")
    list_filter = ("is_active",)
    actions = ("sample_selected_thermometers",)

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
            thermometer.last_reading = reading
            thermometer.last_read_at = timezone.now()
            thermometer.save(update_fields=["last_reading", "last_read_at"])
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

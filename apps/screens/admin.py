from django.contrib import admin

from .models import DeviceScreen


@admin.register(DeviceScreen)
class DeviceScreenAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "category",
        "skin",
        "columns",
        "rows",
        "resolution_width",
        "resolution_height",
    )
    list_filter = ("category",)
    search_fields = ("name", "slug", "skin")

from django.contrib import admin

from .models import DeviceScreen, PyxelViewport


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
        "min_refresh_ms",
    )
    list_filter = ("category",)
    search_fields = ("name", "slug", "skin")


@admin.register(PyxelViewport)
class PyxelViewportAdmin(DeviceScreenAdmin):
    list_display = DeviceScreenAdmin.list_display + (
        "pyxel_fps",
    )
    search_fields = DeviceScreenAdmin.search_fields + ("pyxel_script",)

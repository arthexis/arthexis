from django.contrib import admin

from apps.locals.user_data import EntityModelAdmin

from .models import Widget, WidgetProfile, WidgetZone


@admin.register(WidgetZone)
class WidgetZoneAdmin(EntityModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "slug")


@admin.register(Widget)
class WidgetAdmin(EntityModelAdmin):
    list_display = ("name", "slug", "zone", "is_enabled", "priority")
    list_filter = ("zone", "is_enabled")
    search_fields = ("name", "slug", "renderer_path")
    ordering = ("priority", "name")


@admin.register(WidgetProfile)
class WidgetProfileAdmin(EntityModelAdmin):
    list_display = ("widget", "user", "group", "is_enabled")
    list_filter = ("is_enabled", "group")
    search_fields = ("widget__name", "widget__slug", "user__username", "group__name")

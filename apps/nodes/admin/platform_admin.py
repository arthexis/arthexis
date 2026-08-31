from django.contrib import admin

from apps.locals.user_data import EntityModelAdmin

from ..models import Platform


@admin.register(Platform)
class PlatformAdmin(EntityModelAdmin):
    list_display = ("name", "hardware", "architecture", "os_name", "os_version")
    list_filter = ("architecture", "os_name")
    search_fields = ("name", "hardware", "architecture", "os_name", "os_version")

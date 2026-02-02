from django.contrib import admin

from apps.locals.user_data import EntityModelAdmin

from ..models import AdminNotice


@admin.register(AdminNotice)
class AdminNoticeAdmin(EntityModelAdmin):
    list_display = ("created_at", "dismissed_at", "dismissed_by")
    search_fields = ("message",)
    readonly_fields = ("created_at", "dismissed_at", "dismissed_by")

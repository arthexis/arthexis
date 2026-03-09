from django.contrib import admin

from apps.locals.admin_mixins import EntityModelAdmin

from apps.core.models.admin_notice import AdminNotice


@admin.register(AdminNotice)
class AdminNoticeAdmin(EntityModelAdmin):
    list_display = ("created_at", "dismissed_at", "dismissed_by")
    search_fields = ("message",)
    readonly_fields = ("created_at", "dismissed_at", "dismissed_by")

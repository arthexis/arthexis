from django.contrib import admin

from apps.core.admin.mixins import PublicViewLinksAdminMixin
from .models import LogbookEntry, LogbookLogAttachment


@admin.register(LogbookEntry)
class LogbookEntryAdmin(PublicViewLinksAdminMixin, admin.ModelAdmin):
    """Admin configuration for public logbook entries."""

    list_display = ("title", "secret", "created_at", "event_at", "node")
    readonly_fields = ("secret", "created_at")
    search_fields = ("title", "report", "secret")
    autocomplete_fields = ("node", "user")
    view_on_site = True


@admin.register(LogbookLogAttachment)
class LogbookLogAttachmentAdmin(admin.ModelAdmin):
    list_display = ("original_name", "entry", "size")
    search_fields = ("original_name",)
    autocomplete_fields = ("entry",)

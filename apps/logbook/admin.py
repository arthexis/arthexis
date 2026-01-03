from django.contrib import admin

from .models import LogbookEntry, LogbookLogAttachment


@admin.register(LogbookEntry)
class LogbookEntryAdmin(admin.ModelAdmin):
    list_display = ("title", "secret", "created_at", "event_at", "node")
    readonly_fields = ("secret", "created_at")
    search_fields = ("title", "report", "secret")
    autocomplete_fields = ("node", "user")


@admin.register(LogbookLogAttachment)
class LogbookLogAttachmentAdmin(admin.ModelAdmin):
    list_display = ("original_name", "entry", "size")
    search_fields = ("original_name",)
    autocomplete_fields = ("entry",)

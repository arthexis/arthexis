"""Admin registrations for Shortcut Management."""

from __future__ import annotations

from django.contrib import admin

from apps.core.admin import EntityModelAdmin

from .models import ClipboardPattern, Shortcut


class ClipboardPatternInline(admin.TabularInline):
    """Inline clipboard patterns attached to a client shortcut."""

    model = ClipboardPattern
    extra = 0
    fields = (
        "display",
        "pattern",
        "priority",
        "target_kind",
        "target_identifier",
        "target_payload",
        "is_active",
        "clipboard_output_enabled",
        "keyboard_output_enabled",
        "output_template",
    )


@admin.register(Shortcut)
class ShortcutAdmin(EntityModelAdmin):
    """Admin interface for server/client shortcuts."""

    list_display = ("display", "kind", "key_combo", "target_kind", "target_identifier", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("display", "key_combo")
    inlines = (ClipboardPatternInline,)


@admin.register(ClipboardPattern)
class ClipboardPatternAdmin(EntityModelAdmin):
    """Admin interface for clipboard routing patterns."""

    list_display = ("display", "shortcut", "priority", "target_kind", "target_identifier", "is_active")
    list_filter = ("is_active",)
    search_fields = ("display", "pattern", "shortcut__display")

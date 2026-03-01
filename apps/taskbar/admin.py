"""Admin registrations for taskbar models."""

from django.contrib import admin

from .models import TaskbarIcon, TaskbarMenu, TaskbarMenuAction


class TaskbarMenuActionInline(admin.TabularInline):
    """Inline editor for taskbar actions attached to a menu."""

    model = TaskbarMenuAction
    extra = 0


@admin.register(TaskbarMenu)
class TaskbarMenuAdmin(admin.ModelAdmin):
    """Admin controls for taskbar menus."""

    list_display = ("name", "slug", "left_click_enabled", "right_click_default_enabled")
    search_fields = ("name", "slug")
    inlines = (TaskbarMenuActionInline,)


@admin.register(TaskbarMenuAction)
class TaskbarMenuActionAdmin(admin.ModelAdmin):
    """Admin controls for taskbar menu actions."""

    list_display = ("label", "menu", "action_type", "is_left_click", "is_default_right_click")
    list_filter = ("action_type", "is_left_click", "is_default_right_click")
    search_fields = ("label", "command", "menu__name", "recipe__slug")


@admin.register(TaskbarIcon)
class TaskbarIconAdmin(admin.ModelAdmin):
    """Admin controls for taskbar icons."""

    list_display = ("name", "slug", "is_default")
    list_filter = ("is_default",)
    search_fields = ("name", "slug")

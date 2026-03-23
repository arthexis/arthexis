"""Admin bindings for special command registry models."""

from django.contrib import admin

from apps.special.models import SpecialCommand, SpecialCommandParameter


class SpecialCommandParameterInline(admin.TabularInline):
    """Inline editor for special command parameters."""

    model = SpecialCommandParameter
    extra = 0


@admin.register(SpecialCommand)
class SpecialCommandAdmin(admin.ModelAdmin):
    """Admin options for special command definitions."""

    inlines = [SpecialCommandParameterInline]
    list_display = ("name", "plural_name", "keystone_model", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "plural_name", "keystone_model", "command_path")


@admin.register(SpecialCommandParameter)
class SpecialCommandParameterAdmin(admin.ModelAdmin):
    """Admin options for introspected special command parameters."""

    list_display = (
        "command",
        "name",
        "cli_name",
        "kind",
        "value_type",
        "is_required",
        "allows_multiple",
    )
    list_filter = ("kind", "value_type", "is_required", "allows_multiple")
    search_fields = ("command__name", "name", "cli_name")

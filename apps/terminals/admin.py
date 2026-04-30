from django.contrib import admin

from apps.core.admin import OwnableAdminMixin

from .models import AgentTerminal


@admin.register(AgentTerminal)
class AgentTerminalAdmin(OwnableAdminMixin, admin.ModelAdmin):
    list_display = ("name", "owner_display", "effective_node_role", "auto_close_on_exit", "updated_at")
    list_filter = ("auto_close_on_exit", "prompt_block_mode", "node_role")
    search_fields = ("name", "executable", "launch_command", "launch_prompt")
    readonly_fields = (
        "name",
        "user",
        "group",
        "avatar",
        "node_role",
        "executable",
        "launch_command",
        "launch_prompt",
        "prompt_blocks",
        "auto_close_on_exit",
        "prompt_block_mode",
        "startup_maximized",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related("node_role")
        terminal_role = queryset.model._meta.get_field("node_role").remote_field.model.objects.filter(
            name="Terminal"
        ).first()
        for terminal in queryset:
            terminal._cached_terminal_role = terminal.node_role or terminal_role
        return queryset

from django.contrib import admin

from .models import AgentTerminal


@admin.register(AgentTerminal)
class AgentTerminalAdmin(admin.ModelAdmin):
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

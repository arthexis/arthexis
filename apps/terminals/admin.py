from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.core.admin import OwnableAdminForm, OwnableAdminMixin

from .models import AgentTerminal


class AgentTerminalAdminForm(OwnableAdminForm):
    owner_field_names = ("user", "group", "avatar")
    owner_conflict_message = _("Select only one owner between user, group, and avatar.")
    owner_required_message = _("Profiles must be assigned to a user, security group, or avatar.")


@admin.register(AgentTerminal)
class AgentTerminalAdmin(OwnableAdminMixin, admin.ModelAdmin):
    ownable_fieldset = ("Owner", {"fields": ("user", "group", "avatar")})
    ownable_form_class = AgentTerminalAdminForm
    list_display = ("name", "owner_display", "effective_node_role", "auto_close_on_exit", "updated_at")
    list_filter = ("auto_close_on_exit", "prompt_block_mode", "node_role")
    search_fields = ("name", "executable", "launch_command", "launch_prompt")
    readonly_fields = (
        "name",
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

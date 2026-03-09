import subprocess
from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django.db.models import Count
from django.db.models import Exists, OuterRef
from django.utils.translation import gettext_lazy as _

from apps.core.systemctl import _systemctl_command
from apps.services.lifecycle import SERVICE_NAME_LOCK, lock_dir, read_service_name

from apps.locals.user_data import EntityModelAdmin

from ..models import NodeRole, Node
from .forms import NodeRoleAdminForm


@admin.register(NodeRole)
class NodeRoleAdmin(EntityModelAdmin):
    form = NodeRoleAdminForm
    actions = ("switch_selected_role", "switch_selected_role_and_restart")
    list_display = (
        "name",
        "is_assigned_to_this_node",
        "acronym",
        "description",
        "default_upgrade_policy",
        "registered",
        "default_features",
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        self_nodes = Node.objects.filter(
            current_relation=Node.Relation.SELF,
            role_id=OuterRef("pk"),
        )
        return (
            qs.annotate(
                _registered=Count("node", distinct=True),
                _is_assigned_to_this_node=Exists(self_nodes),
            )
            .prefetch_related("features")
        )

    @admin.display(description="Ours", boolean=True, ordering="_is_assigned_to_this_node")
    def is_assigned_to_this_node(self, obj):
        return bool(getattr(obj, "_is_assigned_to_this_node", False))

    @admin.display(description="Registered", ordering="_registered")
    def registered(self, obj):
        return getattr(obj, "_registered", obj.node_set.count())

    @admin.display(description="Default Node Features")
    def default_features(self, obj):
        features = [feature.display for feature in obj.features.all()]
        return ", ".join(features) if features else "—"

    def save_model(self, request, obj, form, change):
        obj.node_set.set(form.cleaned_data.get("nodes", []))

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            actions.pop("switch_selected_role", None)
            actions.pop("switch_selected_role_and_restart", None)
        return actions

    @admin.action(description="Switch selected role for this node")
    def switch_selected_role(self, request, queryset):
        self._switch_selected_role(request=request, queryset=queryset, restart=False)

    @admin.action(description="Switch selected role for this node and restart service")
    def switch_selected_role_and_restart(self, request, queryset):
        self._switch_selected_role(request=request, queryset=queryset, restart=True)

    def _switch_selected_role(self, *, request, queryset, restart):
        role = queryset.first() if queryset.count() == 1 else None
        if role is None:
            self.message_user(
                request,
                _("Select exactly one role to switch this node."),
                level=messages.ERROR,
            )
            return

        local_node = Node.get_local() or Node.objects.filter(
            current_relation=Node.Relation.SELF
        ).first()
        if local_node is None:
            self.message_user(
                request,
                _("Unable to determine the local node to switch roles."),
                level=messages.ERROR,
            )
            return

        local_node.role = role
        local_node.save(update_fields=["role", "last_updated"])
        self.message_user(
            request,
            _("Switched %(node)s to %(role)s.")
            % {"node": local_node.hostname, "role": role.name},
            level=messages.SUCCESS,
        )

        if restart:
            self._restart_suite_service(request)

    def _restart_suite_service(self, request):
        unit_name = read_service_name(lock_dir(Path(settings.BASE_DIR)) / SERVICE_NAME_LOCK)
        if not unit_name:
            self.message_user(
                request,
                _("No configured suite service was found to restart."),
                level=messages.WARNING,
            )
            return

        command = _systemctl_command()
        if not command:
            self.message_user(
                request,
                _("Systemd controls are unavailable on this node."),
                level=messages.WARNING,
            )
            return

        try:
            subprocess.run(
                [*command, "restart", unit_name],
                check=True,
                cwd=Path(settings.BASE_DIR),
                timeout=30,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            self.message_user(
                request,
                _("Failed to restart %(unit)s.") % {"unit": unit_name},
                level=messages.ERROR,
            )
            return

        self.message_user(
            request,
            _("Restart requested for %(unit)s.") % {"unit": unit_name},
            level=messages.SUCCESS,
        )

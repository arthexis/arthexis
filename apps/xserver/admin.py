from __future__ import annotations

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path
from django.utils.translation import gettext_lazy as _

from apps.discovery.services import record_discovery_item, start_discovery
from apps.locals.user_data import EntityModelAdmin
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment

from .models import XDisplayInstance
from .utils import has_x_server


@admin.register(XDisplayInstance)
class XDisplayInstanceAdmin(EntityModelAdmin):
    """Admin for discovered X display server instances."""

    list_display = (
        "display_name",
        "server_type",
        "runtime_scope",
        "host",
        "process_name",
        "node",
        "detected_at",
    )
    search_fields = (
        "display_name",
        "host",
        "server_type",
        "process_name",
        "node__hostname",
    )

    def get_urls(self):
        """Register custom discover endpoint for changelist usage."""

        custom = [
            path(
                "discover/",
                self.admin_site.admin_view(self.discover_view),
                name="xserver_xdisplayinstance_discover",
            )
        ]
        return custom + super().get_urls()

    def _ensure_feature_enabled(self, request, *, node: Node | None) -> bool:
        """Ensure the x-display feature exists and is assigned to the local node."""

        try:
            feature = NodeFeature.objects.get(slug="x-display-server")
        except NodeFeature.DoesNotExist:
            self.message_user(
                request,
                _("Discover is unavailable because the x-display feature is not configured."),
                level=messages.ERROR,
            )
            return False

        if node is None:
            self.message_user(
                request,
                _("No local node is registered; cannot perform X server discovery."),
                level=messages.ERROR,
            )
            return False

        NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)
        node.sync_feature_tasks()
        return True

    def discover_view(self, request):
        """Run X display server discovery from the admin changelist."""

        node = Node.get_local()
        if not self._ensure_feature_enabled(request, node=node):
            return redirect("..")

        discovery = start_discovery(
            _("Discover"),
            request,
            model=self.model,
            metadata={"action": "xserver_discover"},
        )

        if not has_x_server():
            XDisplayInstance.objects.filter(node=node).delete()
            if discovery:
                discovery.metadata = {"action": "xserver_discover", "result": "no_server"}
                discovery.save(update_fields=["metadata"])
            self.message_user(
                request,
                _("No X display server was detected on this node."),
                level=messages.WARNING,
            )
            return redirect("..")

        created, updated = XDisplayInstance.refresh_from_system(node=node)
        if discovery:
            for instance in XDisplayInstance.objects.filter(node=node):
                record_discovery_item(
                    discovery,
                    obj=instance,
                    label=str(instance),
                    created=bool(created),
                    overwritten=bool(updated),
                    data={
                        "display_name": instance.display_name,
                        "server_type": instance.server_type,
                        "runtime_scope": instance.runtime_scope,
                        "host": instance.host,
                    },
                )
            discovery.metadata = {
                "action": "xserver_discover",
                "created": created,
                "updated": updated,
            }
            discovery.save(update_fields=["metadata"])

        self.message_user(
            request,
            _("X display discovery complete (created: %(created)s, updated: %(updated)s).")
            % {"created": created, "updated": updated},
            level=messages.SUCCESS,
        )
        return redirect("..")

    def get_changelist_instance(self, request):
        """Inject discover action metadata used by discovery syntax UI affordances."""

        changelist = super().get_changelist_instance(request)
        changelist.model_admin = self
        return changelist

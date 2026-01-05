from __future__ import annotations

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.locals.user_data import EntityModelAdmin
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment

from .models import ClockDevice
from .utils import has_clock_device


@admin.register(ClockDevice)
class ClockDeviceAdmin(DjangoObjectActions, EntityModelAdmin):
    list_display = ("address", "bus", "node", "description", "public_view")
    search_fields = ("address", "description", "raw_info", "node__hostname")
    changelist_actions = ["find_clock_devices"]
    change_list_template = "django_object_actions/change_list.html"

    def get_urls(self):
        custom = [
            path(
                "find-clock-devices/",
                self.admin_site.admin_view(self.find_clock_devices_view),
                name="clocks_clockdevice_find_devices",
            ),
        ]
        return custom + super().get_urls()

    def find_clock_devices(self, request, queryset=None):
        return redirect("admin:clocks_clockdevice_find_devices")

    find_clock_devices.label = _("Find Clock Devices")
    find_clock_devices.short_description = _("Find Clock Devices")
    find_clock_devices.changelist = True

    @admin.display(description=_("Public View"))
    def public_view(self, obj):
        if not obj.enable_public_view:
            return _("Disabled")
        if not obj.public_view_slug:
            return _("Missing slug")
        url = reverse("clockdevice-public-view", args=[obj.public_view_slug])
        return format_html('<a href="{}" target="_blank">{}</a>', url, _("Open"))

    def _ensure_rtc_feature_enabled(
        self,
        request,
        action_label: str,
        *,
        node: Node | None = None,
        auto_enable: bool = False,
    ):
        try:
            feature = NodeFeature.objects.get(slug="gpio-rtc")
        except NodeFeature.DoesNotExist:
            self.message_user(
                request,
                _("%(action)s is unavailable because the feature is not configured.")
                % {"action": action_label},
                level=messages.ERROR,
            )
            return None
        if not feature.is_enabled:
            if auto_enable and node:
                NodeFeatureAssignment.objects.update_or_create(
                    node=node, feature=feature
                )
                node.sync_feature_tasks()
                self.message_user(
                    request,
                    _("%(feature)s feature was automatically enabled.")
                    % {"feature": feature.display},
                    level=messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    _("%(feature)s feature is not enabled on this node.")
                    % {"feature": feature.display},
                    level=messages.WARNING,
                )
                return None
        return feature

    def _get_local_node(self, request):
        node = Node.get_local()
        if node is None:
            self.message_user(
                request,
                _("No local node is registered; cannot perform clock actions."),
                level=messages.ERROR,
            )
        return node

    def find_clock_devices_view(self, request):
        node = self._get_local_node(request)
        if node is None:
            return redirect("..")

        feature = self._ensure_rtc_feature_enabled(
            request,
            _("Find Clock Devices"),
            node=node,
            auto_enable=True,
        )
        if not feature:
            return redirect("..")

        if not has_clock_device():
            self.message_user(
                request,
                _("No I2C clock devices were detected on this node."),
                level=messages.WARNING,
            )
            return redirect("..")

        created, updated = ClockDevice.refresh_from_system(node=node)
        if created or updated:
            self.message_user(
                request,
                _("Updated %(created)s new and %(updated)s existing clock devices.")
                % {"created": created, "updated": updated},
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                _("No clock devices were added or updated."),
                level=messages.INFO,
            )
        return redirect("..")

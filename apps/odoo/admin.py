from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.locals.user_data import EntityModelAdmin

from .models import OdooDeployment
from .services import sync_odoo_deployments


@admin.register(OdooDeployment)
class OdooDeploymentAdmin(DjangoObjectActions, EntityModelAdmin):
    actions = ["discover_instances"]
    changelist_actions = ["discover_instances"]

    list_display = (
        "name",
        "config_path",
        "base_path",
        "db_name",
        "db_host",
        "db_port",
        "http_port",
        "last_discovered",
    )
    search_fields = (
        "name",
        "config_path",
        "db_name",
        "db_user",
        "db_host",
    )
    readonly_fields = ("last_discovered",)

    fieldsets = (
        (None, {"fields": ("name", "config_path", "base_path", "last_discovered")}),
        (
            _("Database"),
            {
                "fields": (
                    "db_name",
                    "db_filter",
                    "db_host",
                    "db_port",
                    "db_user",
                    "db_password",
                    "admin_password",
                )
            },
        ),
        (
            _("Runtime"),
            {
                "fields": (
                    "addons_path",
                    "data_dir",
                    "logfile",
                    "http_port",
                    "longpolling_port",
                )
            },
        ),
    )

    def get_urls(self):  # pragma: no cover - admin hook
        urls = super().get_urls()
        custom = [
            path(
                "discover/",
                self.admin_site.admin_view(self.discover_instances_view),
                name="odoo_odoodeployment_discover",
            ),
        ]
        return custom + urls

    def _discover_url(self) -> str:
        return reverse("admin:odoo_odoodeployment_discover")

    def discover_instances(self, request, queryset=None):  # pragma: no cover - admin action
        return HttpResponseRedirect(self._discover_url())

    discover_instances.label = _("Discover Odoo Instances")
    discover_instances.short_description = _("Discover Odoo Instances")
    discover_instances.requires_queryset = False

    def discover_instances_view(self, request):
        opts = self.model._meta
        changelist_url = reverse("admin:odoo_odoodeployment_changelist")
        context = {
            **self.admin_site.each_context(request),
            "opts": opts,
            "title": _("Discover Odoo Instances"),
            "changelist_url": changelist_url,
            "action_url": self._discover_url(),
            "result": None,
        }

        if request.method == "POST":
            if not (
                self.has_view_or_change_permission(request)
                or self.has_add_permission(request)
            ):
                raise PermissionDenied
            result = sync_odoo_deployments(scan_filesystem=False)
            context["result"] = result
            if result["created"] or result["updated"]:
                message = _(
                    "Odoo configuration discovery completed. %(created)s created, %(updated)s updated."
                ) % {"created": result["created"], "updated": result["updated"]}
                self.message_user(
                    request,
                    message,
                    level=messages.SUCCESS,
                )
            if result.get("errors"):
                for error in result["errors"]:
                    self.message_user(request, error, level=messages.WARNING)

        return TemplateResponse(
            request,
            "admin/odoo/odoodeployment/discover.html",
            context,
        )

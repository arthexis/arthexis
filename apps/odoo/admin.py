from django.contrib import admin, messages
from django import forms
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.discovery.services import record_discovery_item, start_discovery
from apps.locals.user_data import EntityModelAdmin

from .models import OdooDeployment, OdooQuery, OdooQueryVariable
from .public_query_features import is_public_query_execution_secure_mode_enabled
from .services import sync_odoo_deployments
from .sync_features import (
    ODOO_SYNC_DEPLOYMENT_DISCOVERY_PARAMETER_KEY,
    is_odoo_sync_integration_enabled,
)


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

    discover_instances.label = _("Discover")
    discover_instances.short_description = _("Discover")
    discover_instances.requires_queryset = False
    discover_instances.is_discover_action = True

    def discover_instances_view(self, request):
        opts = self.model._meta
        changelist_url = reverse("admin:odoo_odoodeployment_changelist")
        context = {
            **self.admin_site.each_context(request),
            "opts": opts,
            "title": _("Discover"),
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
            if not is_odoo_sync_integration_enabled(
                ODOO_SYNC_DEPLOYMENT_DISCOVERY_PARAMETER_KEY,
                default=False,
            ):
                self.message_user(
                    request,
                    _("Odoo deployment discovery sync is disabled by suite feature toggles."),
                    level=messages.ERROR,
                )
                return TemplateResponse(
                    request,
                    "admin/odoo/odoodeployment/discover.html",
                    context,
                )
            result = sync_odoo_deployments(scan_filesystem=False)
            discovery = start_discovery(
                _("Discover"),
                request,
                model=self.model,
                metadata={
                    "action": "odoo_deployment_discovery",
                    "found": result.get("found"),
                },
            )
            if discovery:
                for instance in result.get("created_instances", []):
                    record_discovery_item(
                        discovery,
                        obj=instance,
                        label=instance.name,
                        created=True,
                        overwritten=False,
                        data={"config_path": instance.config_path},
                    )
                for instance in result.get("updated_instances", []):
                    record_discovery_item(
                        discovery,
                        obj=instance,
                        label=instance.name,
                        created=False,
                        overwritten=True,
                        data={"config_path": instance.config_path},
                    )
                discovery.metadata = {
                    "action": "odoo_deployment_discovery",
                    "created": result["created"],
                    "updated": result["updated"],
                    "found": result["found"],
                    "errors": result.get("errors") or [],
                }
                discovery.save(update_fields=["metadata"])
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


class OdooQueryVariableInline(admin.TabularInline):
    model = OdooQueryVariable
    extra = 0
    fields = (
        "sort_order",
        "key",
        "label",
        "input_type",
        "default_value",
        "is_required",
        "help_text",
    )


class OdooQueryAdminForm(forms.ModelForm):
    class Meta:
        model = OdooQuery
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        secure_mode_enabled = is_public_query_execution_secure_mode_enabled(default=False)
        policy = _(
            "Public execution is restricted. Public routes are metadata-only previews; staff can execute with authenticated access."
        )
        if secure_mode_enabled:
            self.fields["enable_public_view"].help_text = _(
                "Enable only when absolutely needed and reviewed. "
            ) + policy
            return
        self.fields["enable_public_view"].help_text = _(
            "This toggle is currently blocked by policy. "
        ) + policy

    def clean_enable_public_view(self):
        enabled = self.cleaned_data.get("enable_public_view", False)
        if enabled and not is_public_query_execution_secure_mode_enabled(default=False):
            raise forms.ValidationError(
                _(
                    "Cannot enable public query execution while secure-mode feature flag is disabled."
                )
            )
        return enabled


@admin.register(OdooQuery)
class OdooQueryAdmin(EntityModelAdmin):
    form = OdooQueryAdminForm
    list_display = (
        "name",
        "model_name",
        "method",
        "profile",
        "enable_public_view",
        "public_view_slug",
    )
    search_fields = ("name", "model_name", "method")
    list_filter = ("enable_public_view", "method")
    readonly_fields = ("public_view_slug", "created_at", "updated_at")
    inlines = [OdooQueryVariableInline]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "description",
                    "profile",
                )
            },
        ),
        (
            _("Query"),
            {
                "fields": (
                    "model_name",
                    "method",
                    "kwquery",
                )
            },
        ),
        (
            _("Public View"),
            {
                "fields": (
                    "enable_public_view",
                    "public_view_slug",
                    "public_title",
                    "public_description",
                )
            },
        ),
        (
            _("Metadata"),
            {"fields": ("created_at", "updated_at")},
        ),
    )

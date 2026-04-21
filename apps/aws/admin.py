from __future__ import annotations

from typing import Iterable

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.discovery.services import record_discovery_item, start_discovery

from .admin_mixins import LightsailFetchAdminMixin
from .forms import FetchDatabaseForm, FetchInstanceForm
from .models import AWSCredentials, LightsailDatabase, LightsailInstance
from .services import (
    LightsailFetchError,
    consolidate_lightsail_instances,
    fetch_lightsail_database,
    fetch_lightsail_instance,
    list_lightsail_instances,
    list_lightsail_regions,
    parse_database_details,
    parse_instance_details,
)


class LightsailActionMixin(DjangoObjectActions):
    """Shared object-action and dashboard-action helpers for AWS admins."""

    changelist_actions: list[str] = []
    dashboard_actions: list[str] = []

    def get_changelist_actions(self, request):  # pragma: no cover - admin hook
        parent = getattr(super(), "get_changelist_actions", None)
        actions: list[str] = []
        if callable(parent):
            existing = parent(request)
            if existing:
                actions.extend(existing)
        for action in getattr(self, "changelist_actions", []):
            if action not in actions:
                actions.append(action)
        return actions

    def get_dashboard_actions(self, request) -> Iterable[str]:
        return getattr(self, "dashboard_actions", [])

    def _default_credentials(self) -> AWSCredentials | None:
        """Return the first configured credential entry when present."""

        return AWSCredentials.objects.order_by("name", "pk").first()

    def _ensure_load_instances_allowed(self, request: HttpRequest) -> None:
        if request.method != "POST":
            raise PermissionDenied
        if not self.has_change_permission(request):
            raise PermissionDenied

    def resolve_credentials(self, form):
        """Resolve selected or inline credential values from a fetch form."""

        credentials = form.cleaned_data.get("credentials")
        access_key = form.cleaned_data.get("access_key_id")
        secret_key = form.cleaned_data.get("secret_access_key")
        created = False
        if credentials is None and access_key and secret_key:
            credentials, created = AWSCredentials.objects.update_or_create(
                access_key_id=access_key,
                defaults={
                    "name": form.cleaned_data.get("credential_label") or access_key,
                    "secret_access_key": secret_key,
                },
            )
        return credentials, created

    def user_input_summary_text(self, obj) -> str:
        credentials_name = obj.credentials.name if obj.credentials else "—"
        return _("name=%(name)s; region=%(region)s; credentials=%(credentials)s") % {
            "name": obj.name,
            "region": obj.region,
            "credentials": credentials_name,
        }

    def _load_instances_for_credentials(
        self,
        *,
        request: HttpRequest,
        credentials: AWSCredentials,
        source_label: str,
    ) -> tuple[int, int]:
        """Load Lightsail instances across regions and consolidate records."""

        created_total = 0
        updated_total = 0
        loaded_regions = 0
        consolidated_items: list[tuple[LightsailInstance, bool]] = []
        for region in list_lightsail_regions():
            details = list_lightsail_instances(region=region, credentials=credentials)
            created_count, updated_count, processed_instances = consolidate_lightsail_instances(
                region=region,
                details=details,
                credentials=credentials,
            )
            created_total += created_count
            updated_total += updated_count
            loaded_regions += 1
            consolidated_items.extend(processed_instances)

        discovery = start_discovery(
            _("Load Instances"),
            request,
            model=LightsailInstance,
            metadata={"action": "aws_lightsail_instances_load", "source": source_label},
        )
        if discovery:
            for instance, created in consolidated_items:
                record_discovery_item(
                    discovery,
                    obj=instance,
                    label=instance.name,
                    created=created,
                    overwritten=not created,
                    data={"region": instance.region},
                )

        self.message_user(
            request,
            _("Loaded instances for %(credential)s across %(regions)s regions: %(created)s created, %(updated)s updated.")
            % {
                "credential": credentials.name,
                "regions": loaded_regions,
                "created": created_total,
                "updated": updated_total,
            },
            messages.SUCCESS,
        )
        return created_total, updated_total


@admin.register(AWSCredentials)
class AWSCredentialsAdmin(LightsailActionMixin, admin.ModelAdmin):
    """Admin for stored AWS credential sets."""

    actions = ["load_instances_for_selected"]
    changelist_actions = ["load_instances"]
    dashboard_actions = ["load_instances"]
    list_display = ("name", "access_key_id", "created_at")
    search_fields = ("name", "access_key_id")
    readonly_fields = ("created_at",)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["secret_access_key"].widget = forms.PasswordInput(render_value=False)
        return form

    def load_instances(self, request, queryset=None):  # pragma: no cover - admin action
        """Tool action that loads instances with the first credential set."""

        self._ensure_load_instances_allowed(request)

        credentials = self._default_credentials()
        if credentials is None:
            self.message_user(request, _("Create AWS credentials before loading instances."), messages.ERROR)
            return HttpResponseRedirect(reverse("admin:aws_awscredentials_changelist"))

        try:
            self._load_instances_for_credentials(
                request=request,
                credentials=credentials,
                source_label="credentials_tool",
            )
        except LightsailFetchError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        return HttpResponseRedirect(reverse("admin:aws_lightsailinstance_changelist"))

    load_instances.label = _("Load Instances")
    load_instances.short_description = _("Load Instances")
    load_instances.requires_queryset = False

    @admin.action(description=_("Load instances for selected"))
    def load_instances_for_selected(self, request, queryset):
        """Bulk action that loads instances for selected credential entries."""

        if not queryset.exists():
            self.message_user(request, _("Select at least one credential."), messages.WARNING)
            return

        for credentials in queryset.order_by("name", "pk"):
            try:
                self._load_instances_for_credentials(
                    request=request,
                    credentials=credentials,
                    source_label="credentials_selected",
                )
            except LightsailFetchError as exc:
                self.message_user(
                    request,
                    _("Could not load instances for %(credential)s: %(error)s")
                    % {"credential": credentials.name, "error": exc},
                    messages.ERROR,
                )


@admin.register(LightsailInstance)
class LightsailInstanceAdmin(LightsailFetchAdminMixin, LightsailActionMixin, admin.ModelAdmin):
    fetch_route_name = "aws_lightsailinstance_fetch"
    fetch_template_name = "admin/aws/lightsailinstance/fetch.html"
    fetch_title = _("Fetch Lightsail Instance")
    fetch_form_class = FetchInstanceForm
    fetch_permission_method = "has_change_permission"
    fetch_service = staticmethod(fetch_lightsail_instance)
    fetch_parse_details = staticmethod(parse_instance_details)
    fetch_update_or_create_target = staticmethod(LightsailInstance.objects.update_or_create)
    fetch_discovery_action = "aws_lightsail_instance"
    fetch_success_noun = _("Instance")

    actions = ["fetch", "load_instances"]
    changelist_actions = ["fetch", "load_instances"]
    dashboard_actions = ["fetch", "load_instances"]
    list_display = (
        "user_input_summary",
        "discovered_summary",
        "state",
        "public_ip",
        "private_ip",
        "availability_zone",
    )
    search_fields = (
        "name",
        "region",
        "arn",
        "support_code",
        "public_ip",
        "private_ip",
    )
    list_filter = ("region", "availability_zone", "state")
    readonly_fields = (
        "created_at",
        "raw_details",
    )
    autocomplete_fields = ("credentials",)

    @admin.display(description=_("User-provided fields"))
    def user_input_summary(self, obj):
        return self.user_input_summary_text(obj)

    @admin.display(description=_("Discovered fields"))
    def discovered_summary(self, obj):
        return _("bundle=%(bundle)s; blueprint=%(blueprint)s; arn=%(arn)s") % {
            "bundle": obj.bundle_id or "—",
            "blueprint": obj.blueprint_id or "—",
            "arn": obj.arn or "—",
        }

    def load_instances(self, request, queryset=None):  # pragma: no cover - admin action
        """Load and consolidate Lightsail instances for configured credentials."""

        self._ensure_load_instances_allowed(request)

        credentials = self._default_credentials()
        if credentials is None:
            self.message_user(request, _("Create AWS credentials before loading instances."), messages.ERROR)
            return HttpResponseRedirect(reverse("admin:aws_awscredentials_changelist"))

        try:
            self._load_instances_for_credentials(
                request=request,
                credentials=credentials,
                source_label="instance_tool",
            )
        except LightsailFetchError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        return HttpResponseRedirect(reverse("admin:aws_lightsailinstance_changelist"))

    load_instances.label = _("Load Instances")
    load_instances.short_description = _("Load Instances")
    load_instances.requires_queryset = False



@admin.register(LightsailDatabase)
class LightsailDatabaseAdmin(LightsailFetchAdminMixin, LightsailActionMixin, admin.ModelAdmin):
    fetch_route_name = "aws_lightsaildatabase_fetch"
    fetch_template_name = "admin/aws/lightsaildatabase/fetch.html"
    fetch_title = _("Fetch Lightsail Database")
    fetch_form_class = FetchDatabaseForm
    fetch_permission_method = "has_view_or_change_permission"
    fetch_service = staticmethod(fetch_lightsail_database)
    fetch_parse_details = staticmethod(parse_database_details)
    fetch_update_or_create_target = staticmethod(LightsailDatabase.objects.update_or_create)
    fetch_discovery_action = "aws_lightsail_database"
    fetch_success_noun = _("Database")

    actions = ["fetch"]
    changelist_actions = ["fetch"]
    dashboard_actions = ["fetch"]
    list_display = (
        "user_input_summary",
        "discovered_summary",
        "state",
        "engine",
        "engine_version",
        "availability_zone",
        "secondary_availability_zone",
    )
    search_fields = (
        "name",
        "region",
        "arn",
        "engine",
        "engine_version",
    )
    list_filter = ("region", "availability_zone", "state", "engine")
    readonly_fields = (
        "created_at",
        "raw_details",
    )
    autocomplete_fields = ("credentials",)

    @admin.display(description=_("User-provided fields"))
    def user_input_summary(self, obj):
        return self.user_input_summary_text(obj)

    @admin.display(description=_("Discovered fields"))
    def discovered_summary(self, obj):
        return _("arn=%(arn)s; endpoint=%(endpoint)s:%(port)s") % {
            "arn": obj.arn or "—",
            "endpoint": obj.endpoint_address or "—",
            "port": obj.endpoint_port or "—",
        }

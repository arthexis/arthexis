from __future__ import annotations

from typing import Iterable

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.discovery.services import record_discovery_item, start_discovery

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
class LightsailInstanceAdmin(LightsailActionMixin, admin.ModelAdmin):
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

    def get_urls(self):  # pragma: no cover - admin hook
        urls = super().get_urls()
        custom = [
            path(
                "fetch/",
                self.admin_site.admin_view(self.fetch_view),
                name="aws_lightsailinstance_fetch",
            ),
        ]
        return custom + urls

    def _action_url(self):
        return reverse("admin:aws_lightsailinstance_fetch")

    def fetch(self, request, queryset=None):  # pragma: no cover - admin action
        return HttpResponseRedirect(self._action_url())

    fetch.label = _("Discover")
    fetch.short_description = _("Discover")
    fetch.requires_queryset = False
    fetch.is_discover_action = True

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

    def fetch_view(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied

        opts = self.model._meta
        changelist_url = reverse("admin:aws_lightsailinstance_changelist")
        form = FetchInstanceForm(request.POST or None)
        context = {
            **self.admin_site.each_context(request),
            "opts": opts,
            "title": _("Fetch Lightsail Instance"),
            "changelist_url": changelist_url,
            "action_url": self._action_url(),
            "form": form,
        }

        if request.method == "POST" and form.is_valid():
            credentials, created_credentials = self.resolve_credentials(form)
            try:
                details = fetch_lightsail_instance(
                    name=form.cleaned_data["name"],
                    region=form.cleaned_data["region"],
                    credentials=credentials,
                    access_key_id=form.cleaned_data.get("access_key_id"),
                    secret_access_key=form.cleaned_data.get("secret_access_key"),
                )
            except LightsailFetchError as exc:
                self.message_user(request, str(exc), messages.ERROR)
            else:
                defaults = parse_instance_details(details)
                defaults.update(
                    {
                        "region": form.cleaned_data["region"],
                        "credentials": credentials,
                    }
                )
                instance, created = LightsailInstance.objects.update_or_create(
                    name=form.cleaned_data["name"],
                    region=form.cleaned_data["region"],
                    defaults=defaults,
                )
                discovery = start_discovery(
                    _("Discover"),
                    request,
                    model=self.model,
                    metadata={
                        "action": "aws_lightsail_instance",
                        "region": form.cleaned_data["region"],
                    },
                )
                if discovery:
                    record_discovery_item(
                        discovery,
                        obj=instance,
                        label=instance.name,
                        created=created,
                        overwritten=not created,
                        data={"region": instance.region},
                    )
                if created:
                    self.message_user(
                        request,
                        _("Instance %(name)s created from AWS data.") % {"name": instance.name},
                        messages.SUCCESS,
                    )
                else:
                    self.message_user(
                        request,
                        _("Instance %(name)s updated from AWS data.") % {"name": instance.name},
                        messages.SUCCESS,
                    )
                if created_credentials:
                    self.message_user(
                        request,
                        _("Stored new AWS credentials linked to this instance."),
                        messages.INFO,
                    )
                return HttpResponseRedirect(changelist_url)

        return TemplateResponse(
            request,
            "admin/aws/lightsailinstance/fetch.html",
            context,
        )


@admin.register(LightsailDatabase)
class LightsailDatabaseAdmin(LightsailActionMixin, admin.ModelAdmin):
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

    def get_urls(self):  # pragma: no cover - admin hook
        urls = super().get_urls()
        custom = [
            path(
                "fetch/",
                self.admin_site.admin_view(self.fetch_view),
                name="aws_lightsaildatabase_fetch",
            ),
        ]
        return custom + urls

    def _action_url(self):
        return reverse("admin:aws_lightsaildatabase_fetch")

    def fetch(self, request, queryset=None):  # pragma: no cover - admin action
        return HttpResponseRedirect(self._action_url())

    fetch.label = _("Discover")
    fetch.short_description = _("Discover")
    fetch.requires_queryset = False
    fetch.is_discover_action = True

    def fetch_view(self, request):
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied

        opts = self.model._meta
        changelist_url = reverse("admin:aws_lightsaildatabase_changelist")
        form = FetchDatabaseForm(request.POST or None)
        context = {
            **self.admin_site.each_context(request),
            "opts": opts,
            "title": _("Fetch Lightsail Database"),
            "changelist_url": changelist_url,
            "action_url": self._action_url(),
            "form": form,
        }

        if request.method == "POST" and form.is_valid():
            credentials, created_credentials = self.resolve_credentials(form)
            try:
                details = fetch_lightsail_database(
                    name=form.cleaned_data["name"],
                    region=form.cleaned_data["region"],
                    credentials=credentials,
                    access_key_id=form.cleaned_data.get("access_key_id"),
                    secret_access_key=form.cleaned_data.get("secret_access_key"),
                )
            except LightsailFetchError as exc:
                self.message_user(request, str(exc), messages.ERROR)
            else:
                defaults = parse_database_details(details)
                defaults.update(
                    {
                        "region": form.cleaned_data["region"],
                        "credentials": credentials,
                    }
                )
                database, created = LightsailDatabase.objects.update_or_create(
                    name=form.cleaned_data["name"],
                    region=form.cleaned_data["region"],
                    defaults=defaults,
                )
                discovery = start_discovery(
                    _("Discover"),
                    request,
                    model=self.model,
                    metadata={
                        "action": "aws_lightsail_database",
                        "region": form.cleaned_data["region"],
                    },
                )
                if discovery:
                    record_discovery_item(
                        discovery,
                        obj=database,
                        label=database.name,
                        created=created,
                        overwritten=not created,
                        data={"region": database.region},
                    )
                if created:
                    self.message_user(
                        request,
                        _("Database %(name)s created from AWS data.") % {"name": database.name},
                        messages.SUCCESS,
                    )
                else:
                    self.message_user(
                        request,
                        _("Database %(name)s updated from AWS data.") % {"name": database.name},
                        messages.SUCCESS,
                    )
                if created_credentials:
                    self.message_user(
                        request,
                        _("Stored new AWS credentials linked to this database."),
                        messages.INFO,
                    )
                return HttpResponseRedirect(changelist_url)

        return TemplateResponse(
            request,
            "admin/aws/lightsaildatabase/fetch.html",
            context,
        )

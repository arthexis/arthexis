from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.discovery.services import record_discovery_item, start_discovery

from .forms import FetchDatabaseForm, FetchInstanceForm
from .models import AWSCredentials, LightsailDatabase, LightsailInstance
from .services import (
    LightsailFetchError,
    fetch_lightsail_database,
    fetch_lightsail_instance,
    parse_database_details,
    parse_instance_details,
    sync_lightsail_instances,
)


@dataclass
class InstanceSyncResult:
    """Aggregate result metadata for Lightsail instance synchronization."""

    created: int = 0
    updated: int = 0
    conflicts: int = 0

    @property
    def total(self) -> int:
        """Return the combined number of synchronized records."""

        return self.created + self.updated


class LightsailActionMixin(DjangoObjectActions):
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

    def resolve_credentials(self, form):
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


@admin.register(AWSCredentials)
class AWSCredentialsAdmin(LightsailActionMixin, admin.ModelAdmin):
    """Admin tools for stored AWS credentials."""

    actions = ["load_instances", "load_instances_for_selected"]
    changelist_actions = ["load_instances"]
    dashboard_actions = ["load_instances"]
    list_display = ("name", "access_key_id", "created_at")
    search_fields = ("name", "access_key_id")
    readonly_fields = ("created_at",)


    def __init__(self, model, admin_site):
        """Use the AWS credentials-specific changelist template."""

        super().__init__(model, admin_site)
        self.change_list_template = "admin/aws/awscredentials/change_list.html"

    def get_urls(self):  # pragma: no cover - admin hook
        urls = super().get_urls()
        custom = [
            path(
                "load-instances/",
                self.admin_site.admin_view(self.load_instances_view),
                name="aws_awscredentials_load_instances",
            ),
        ]
        return custom + urls

    def _sync_credentials(
        self,
        request,
        credentials: Iterable[AWSCredentials],
        *,
        discovery_label: str,
    ) -> InstanceSyncResult:
        """Synchronize Lightsail instances for the provided credential set."""

        result = InstanceSyncResult()
        selected = list(credentials)
        if not selected:
            self.message_user(
                request,
                _("Select at least one credential."),
                messages.WARNING,
            )
            return result

        discovery = start_discovery(
            discovery_label,
            request,
            model=LightsailInstance,
            metadata={"action": "aws_lightsail_instance_bulk_load"},
        )

        for credential in selected:
            try:
                summary = sync_lightsail_instances(credentials=credential)
            except LightsailFetchError as exc:
                self.message_user(
                    request,
                    _("Credential %(name)s failed: %(error)s")
                    % {"name": credential.name, "error": exc},
                    messages.ERROR,
                )
                continue

            result.created += summary["created"]
            result.updated += summary["updated"]
            result.conflicts += summary.get("conflicts", 0)

            if discovery:
                for instance in summary["instances"]:
                    record_discovery_item(
                        discovery,
                        obj=instance,
                        label=instance.name,
                        created=instance.pk in summary["created_ids"],
                        overwritten=instance.pk in summary["updated_ids"],
                        data={"region": instance.region},
                    )

        if result.total:
            self.message_user(
                request,
                _(
                    "Loaded %(total)s instances (%(created)s created, %(updated)s updated)."
                )
                % {
                    "total": result.total,
                    "created": result.created,
                    "updated": result.updated,
                },
                messages.SUCCESS,
            )
        if result.conflicts:
            self.message_user(
                request,
                _(
                    "Skipped %(count)s instances due to credential conflicts on existing rows."
                )
                % {"count": result.conflicts},
                messages.WARNING,
            )
        return result

    def load_instances_view(self, request):
        """Execute non-queryset credential action from changelist/dashboard tools."""

        if not self.has_change_permission(request):
            raise PermissionDenied
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        self._sync_credentials(
            request,
            AWSCredentials.objects.order_by("name"),
            discovery_label=_("Load Instances"),
        )
        return HttpResponseRedirect(reverse("admin:aws_awscredentials_changelist"))

    def load_instances(self, request, queryset=None):  # pragma: no cover - admin action
        self._sync_credentials(
            request,
            AWSCredentials.objects.order_by("name"),
            discovery_label=_("Load Instances"),
        )
        return HttpResponseRedirect(reverse("admin:aws_awscredentials_changelist"))

    load_instances.label = _("Load Instances")
    load_instances.short_description = _("Load Instances")
    load_instances.requires_queryset = False

    def load_instances_for_selected(self, request, queryset):
        """Synchronize Lightsail instances for selected AWS credential rows."""

        self._sync_credentials(
            request,
            queryset.order_by("name"),
            discovery_label=_("Load Instances for selected credentials"),
        )

    load_instances_for_selected.short_description = _("Load instances for selected")


@admin.register(LightsailInstance)
class LightsailInstanceAdmin(LightsailActionMixin, admin.ModelAdmin):
    actions = ["fetch", "load_instances"]
    changelist_actions = ["fetch", "load_instances"]
    dashboard_actions = ["fetch", "load_instances"]
    list_display = (
        "name",
        "region",
        "state",
        "public_ip",
        "private_ip",
        "bundle_id",
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


    def __init__(self, model, admin_site):
        """Use the Lightsail instance-specific changelist template."""

        super().__init__(model, admin_site)
        self.change_list_template = "admin/aws/lightsailinstance/change_list.html"

    def get_urls(self):  # pragma: no cover - admin hook
        urls = super().get_urls()
        custom = [
            path(
                "fetch/",
                self.admin_site.admin_view(self.fetch_view),
                name="aws_lightsailinstance_fetch",
            ),
            path(
                "load-instances/",
                self.admin_site.admin_view(self.load_instances_view),
                name="aws_lightsailinstance_load_instances",
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
        return HttpResponseRedirect(reverse("admin:aws_lightsailinstance_load_instances"))

    load_instances.label = _("Load Instances")
    load_instances.short_description = _("Load Instances")
    load_instances.requires_queryset = False

    def load_instances_view(self, request):
        """Synchronize instances using stored credentials or environment credentials."""

        if not self.has_change_permission(request):
            raise PermissionDenied
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        credentials = list(AWSCredentials.objects.order_by("name"))
        discovery = start_discovery(
            _("Load Instances"),
            request,
            model=self.model,
            metadata={"action": "aws_lightsail_instance_bulk_load"},
        )

        created_count = 0
        updated_count = 0
        if credentials:
            credential_groups: list[AWSCredentials | None] = credentials
        else:
            credential_groups = [None]

        for credential in credential_groups:
            try:
                summary = sync_lightsail_instances(credentials=credential)
            except LightsailFetchError as exc:
                credential_name = credential.name if credential else _("environment")
                self.message_user(
                    request,
                    _("Load failed for %(credential)s: %(error)s")
                    % {"credential": credential_name, "error": exc},
                    messages.ERROR,
                )
                continue

            created_count += summary["created"]
            updated_count += summary["updated"]
            conflict_count = summary.get("conflicts", 0)
            if conflict_count:
                self.message_user(
                    request,
                    _(
                        "Skipped %(count)s instances due to credential conflicts on existing rows."
                    )
                    % {"count": conflict_count},
                    messages.WARNING,
                )
            if discovery:
                for instance in summary["instances"]:
                    record_discovery_item(
                        discovery,
                        obj=instance,
                        label=instance.name,
                        created=instance.pk in summary["created_ids"],
                        overwritten=instance.pk in summary["updated_ids"],
                        data={"region": instance.region},
                    )

        if created_count or updated_count:
            self.message_user(
                request,
                _(
                    "Loaded %(total)s instances (%(created)s created, %(updated)s updated)."
                )
                % {
                    "total": created_count + updated_count,
                    "created": created_count,
                    "updated": updated_count,
                },
                messages.SUCCESS,
            )
        else:
            self.message_user(request, _("No instances were loaded."), messages.WARNING)

        return HttpResponseRedirect(reverse("admin:aws_lightsailinstance_changelist"))

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
                        _("Instance %(name)s created from AWS data.")
                        % {"name": instance.name},
                        messages.SUCCESS,
                    )
                else:
                    self.message_user(
                        request,
                        _("Instance %(name)s updated from AWS data.")
                        % {"name": instance.name},
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
        "name",
        "region",
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
        if not self.has_change_permission(request):
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
                        _("Database %(name)s created from AWS data.")
                        % {"name": database.name},
                        messages.SUCCESS,
                    )
                else:
                    self.message_user(
                        request,
                        _("Database %(name)s updated from AWS data.")
                        % {"name": database.name},
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

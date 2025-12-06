from __future__ import annotations

from typing import Iterable

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from .forms import FetchDatabaseForm, FetchInstanceForm
from .models import AWSCredentials, LightsailDatabase, LightsailInstance
from .services import (
    LightsailFetchError,
    fetch_lightsail_database,
    fetch_lightsail_instance,
    parse_database_details,
    parse_instance_details,
)


@admin.register(AWSCredentials)
class AWSCredentialsAdmin(admin.ModelAdmin):
    list_display = ("name", "access_key_id", "created_at")
    search_fields = ("name", "access_key_id")
    readonly_fields = ("created_at",)


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


@admin.register(LightsailInstance)
class LightsailInstanceAdmin(LightsailActionMixin, admin.ModelAdmin):
    actions = ["fetch_instance"]
    changelist_actions = ["fetch_instance"]
    dashboard_actions = ["fetch_instance"]
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

    def get_urls(self):  # pragma: no cover - admin hook
        urls = super().get_urls()
        custom = [
            path(
                "fetch/",
                self.admin_site.admin_view(self.fetch_instance_view),
                name="aws_lightsailinstance_fetch",
            ),
        ]
        return custom + urls

    def _action_url(self):
        return reverse("admin:aws_lightsailinstance_fetch")

    def fetch_instance(self, request, queryset=None):  # pragma: no cover - admin action
        return HttpResponseRedirect(self._action_url())

    fetch_instance.label = _("Fetch Instance")
    fetch_instance.short_description = _("Fetch Instance")
    fetch_instance.requires_queryset = False

    def fetch_instance_view(self, request):
        if not self.has_view_or_change_permission(request):
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
    actions = ["fetch_database"]
    changelist_actions = ["fetch_database"]
    dashboard_actions = ["fetch_database"]
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
                self.admin_site.admin_view(self.fetch_database_view),
                name="aws_lightsaildatabase_fetch",
            ),
        ]
        return custom + urls

    def _action_url(self):
        return reverse("admin:aws_lightsaildatabase_fetch")

    def fetch_database(self, request, queryset=None):  # pragma: no cover - admin action
        return HttpResponseRedirect(self._action_url())

    fetch_database.label = _("Fetch Database")
    fetch_database.short_description = _("Fetch Database")
    fetch_database.requires_queryset = False

    def fetch_database_view(self, request):
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

"""Admin integrations for remote actions and bearer tokens."""

from __future__ import annotations

import yaml
from django.contrib import admin, messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import path
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.actions.models import RemoteAction, RemoteActionToken
from apps.actions.openapi import build_openapi_spec
from apps.core.admin import OwnableAdminMixin
from apps.locals.user_data import EntityModelAdmin


@admin.register(RemoteAction)
class RemoteActionAdmin(DjangoObjectActions, OwnableAdminMixin, EntityModelAdmin):
    """Manage remote actions and export OpenAPI specs from the changelist."""

    list_display = ("display", "slug", "operation_id", "recipe", "owner", "is_active")
    list_filter = ("is_active",)
    search_fields = ("display", "slug", "operation_id")
    readonly_fields = ("uuid", "created_at", "updated_at")
    actions = ("generate_openapi_for_selected",)
    changelist_actions = ["my_openapi_spec"]
    change_list_template = "django_object_actions/change_list.html"

    def get_urls(self):
        """Register custom admin endpoints for OpenAPI output."""

        custom_urls = [
            path(
                "my-openapi-spec/",
                self.admin_site.admin_view(self.my_openapi_spec_view),
                name="actions_remoteaction_my_openapi_spec",
            )
        ]
        return custom_urls + super().get_urls()

    def my_openapi_spec(self, request, queryset=None):
        """Redirect the changelist tool to the current user's OpenAPI view."""

        return redirect("admin:actions_remoteaction_my_openapi_spec")

    my_openapi_spec.label = _("My OpenAPI Spec")
    my_openapi_spec.short_description = _("My OpenAPI Spec")
    my_openapi_spec.changelist = True

    def my_openapi_spec_view(self, request):
        """Render a YAML OpenAPI spec for actions available to the current user."""

        spec = build_openapi_spec(user=request.user, request=request)
        payload = yaml.safe_dump(spec, sort_keys=False)
        return HttpResponse(payload, content_type="application/yaml")

    @admin.action(description=_("Generate OpenAPI spec for selected Remote Actions"))
    def generate_openapi_for_selected(self, request, queryset):
        """Return a YAML spec that includes only selected actions."""

        if not queryset.exists():
            self.message_user(request, _("No actions were selected."), level=messages.WARNING)
            return
        spec = build_openapi_spec(actions=queryset, user=request.user, request=request)
        payload = yaml.safe_dump(spec, sort_keys=False)
        return HttpResponse(payload, content_type="application/yaml")


@admin.register(RemoteActionToken)
class RemoteActionTokenAdmin(EntityModelAdmin):
    """Issue and manage bearer tokens used by remote actors."""

    list_display = (
        "user",
        "label",
        "key_prefix",
        "expires_at",
        "is_active",
        "last_used_at",
        "created_at",
    )
    list_filter = ("is_active",)
    search_fields = ("user__username", "label", "key_prefix")
    readonly_fields = ("key_prefix", "key_hash", "last_used_at", "created_at")

    def save_model(self, request, obj, form, change):
        """Ensure tokens created in admin always get a valid hashed bearer value."""

        if change:
            super().save_model(request, obj, form, change)
            return

        token, raw_key = RemoteActionToken.issue_for_user(
            obj.user,
            label=obj.label,
            expires_at=obj.expires_at,
        )
        if token.is_active != obj.is_active:
            token.is_active = obj.is_active
            token.save(update_fields=["is_active"])

        messages.success(
            request,
            _("Bearer token created. Copy it now — it will not be shown again: %(token)s")
            % {"token": raw_key},
        )

        obj.pk = token.pk
        obj.key_prefix = token.key_prefix
        obj.key_hash = token.key_hash
        obj.is_active = token.is_active
        obj.last_used_at = token.last_used_at
        obj.created_at = token.created_at


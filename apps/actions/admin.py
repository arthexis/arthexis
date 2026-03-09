"""Admin integrations for remote actions and bearer tokens."""

from __future__ import annotations

import logging

import yaml
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.actions.models import (
    DashboardAction,
    RemoteAction,
    RemoteActionToken,
    StaffTask,
    StaffTaskPreference,
)
from apps.actions.openapi import build_openapi_spec
from apps.core.admin import OwnableAdminMixin
from apps.locals.admin_mixins import EntityModelAdmin

logger = logging.getLogger(__name__)


@admin.register(StaffTask)
class StaffTaskAdmin(EntityModelAdmin):
    """Manage available dashboard staff tasks and default visibility."""

    list_display = (
        "label",
        "slug",
        "admin_url_name",
        "order",
        "default_enabled",
        "superuser_only",
        "is_active",
    )
    list_filter = ("default_enabled", "superuser_only", "is_active")
    search_fields = ("label", "slug", "admin_url_name", "description")


@admin.register(StaffTaskPreference)
class StaffTaskPreferenceAdmin(EntityModelAdmin):
    """Inspect per-user staff task visibility overrides."""

    list_display = ("user", "task", "is_enabled", "updated_at")
    list_filter = ("is_enabled", "task")
    search_fields = ("user__username", "task__label", "task__slug")


@admin.register(DashboardAction)
class DashboardActionAdmin(EntityModelAdmin):
    """Manage declarative admin-dashboard model-row actions."""

    list_display = (
        "label",
        "content_type",
        "target_type",
        "http_method",
        "is_active",
        "order",
    )
    list_filter = ("target_type", "http_method", "is_active", "content_type__app_label")
    search_fields = ("label", "slug", "admin_url_name", "absolute_url", "caller_sigil")

    def get_urls(self):
        """Expose an execution endpoint used by recipe-backed dashboard actions."""

        custom_urls = [
            path(
                "<int:action_id>/execute/",
                self.admin_site.admin_view(self.execute_view),
                name="actions_dashboardaction_execute",
            )
        ]
        return custom_urls + super().get_urls()

    def execute_view(self, request, action_id: int):
        """Execute a recipe-backed dashboard action and return to the admin index."""

        if request.method.lower() != "post":
            raise PermissionDenied
        action = (
            DashboardAction.objects.filter(
                pk=action_id,
                is_active=True,
                recipe__isnull=False,
                target_type=DashboardAction.TargetType.RECIPE,
                http_method=DashboardAction.HttpMethod.POST,
            )
            .select_related("recipe", "content_type")
            .first()
        )
        if action is None:
            raise PermissionDenied
        if not self.has_change_permission(request, action):
            raise PermissionDenied
        if action.caller_sigil and not DashboardAction._is_safe_caller_sigil(action.caller_sigil):
            self.message_user(
                request,
                _("Dashboard action '%(label)s' failed: invalid caller sigil.")
                % {"label": action.label},
                level=messages.ERROR,
            )
            return redirect("admin:index")

        try:
            execution = action.recipe.execute(caller=action.caller_sigil or action.content_type.app_label)
        except Exception as exc:  # pragma: no cover - defensive: recipe runtime failures are dynamic
            logger.exception("Dashboard action execution failed", extra={"action_id": action.pk})
            self.message_user(
                request,
                _("Dashboard action '%(label)s' failed: %(error)s")
                % {"label": action.label, "error": exc},
                level=messages.ERROR,
            )
        else:
            self.message_user(
                request,
                _("Dashboard action '%(label)s' executed. Result: %(result)s")
                % {"label": action.label, "result": execution.result},
                level=messages.SUCCESS,
            )
        return redirect("admin:index")


@admin.register(RemoteAction)
class RemoteActionAdmin(DjangoObjectActions, OwnableAdminMixin, EntityModelAdmin):
    """Manage remote actions and export OpenAPI specs from the changelist."""

    OPENAPI_EXPORT_FILENAME = "my-actions-openapi.yaml"

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
        """Preview the current user's OpenAPI YAML and optionally download it."""

        if not self.has_view_or_change_permission(request):
            raise PermissionDenied

        spec = build_openapi_spec(user=request.user, request=request)
        payload = yaml.safe_dump(spec, sort_keys=False)
        if request.GET.get("download") == "1":
            response = HttpResponse(payload, content_type="application/yaml")
            response["Content-Disposition"] = (
                f'attachment; filename="{self.OPENAPI_EXPORT_FILENAME}"'
            )
            return response

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("My OpenAPI Spec"),
            "payload": payload,
            "download_url": f"{request.path}?download=1",
            "actions_changelist_url": reverse("admin:actions_remoteaction_changelist"),
        }
        return TemplateResponse(request, "admin/actions/remoteaction/openapi_preview.html", context)

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
class RemoteActionTokenAdmin(DjangoObjectActions, EntityModelAdmin):
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
    actions = ("generate_token",)
    changelist_actions = ["generate_token"]
    change_list_template = "django_object_actions/change_list.html"
    fieldsets = (
        (_("Details"), {"fields": ("label", "expires_at", "is_active")}),
        (
            _("Security"),
            {"fields": ("key_prefix", "key_hash", "last_used_at", "created_at")},
        ),
        (_("Owner"), {"fields": ("user",)}),
    )

    def get_changelist_actions(self, request):  # pragma: no cover - admin hook
        """Expose tool actions to dashboard templates that inspect changelist actions."""

        parent = getattr(super(), "get_changelist_actions", None)
        actions = []
        if callable(parent):
            existing = parent(request)
            if existing:
                actions.extend(existing)
        for action in self.changelist_actions:
            if action not in actions:
                actions.append(action)
        return actions

    def get_urls(self):
        """Register custom admin endpoint used by changelist and dashboard tools."""

        custom_urls = [
            path(
                "generate-token/",
                self.admin_site.admin_view(self.generate_token_view),
                name="actions_remoteactiontoken_generate_token",
            )
        ]
        return custom_urls + super().get_urls()

    def get_changeform_initial_data(self, request):
        """Default owner and expiry when manually creating a token in admin."""

        initial = super().get_changeform_initial_data(request)
        initial.setdefault("user", request.user.pk)
        initial.setdefault(
            "expires_at",
            timezone.localtime(timezone.now() + RemoteActionToken.DEFAULT_EXPIRATION),
        )
        return initial

    def _issue_default_token_for_request_user(self, request) -> str:
        """Issue a token for the current user and return its one-time raw key."""

        _token, raw_key = RemoteActionToken.issue_for_user(
            request.user,
            expires_at=timezone.now() + RemoteActionToken.DEFAULT_EXPIRATION,
        )
        return raw_key

    @admin.action(description=_("Generate Token"))
    def generate_token(self, request, queryset=None):
        """Redirect object-tool action to one-click token generation endpoint."""

        return redirect(reverse("admin:actions_remoteactiontoken_generate_token"))

    generate_token.label = _("Generate Token")
    generate_token.short_description = _("Generate Token")
    generate_token.changelist = True
    generate_token.requires_queryset = False

    def generate_token_view(self, request):
        """Generate a bearer token for the current user from dashboard/changelist."""

        if not self.has_add_permission(request):
            raise PermissionDenied

        raw_key = self._issue_default_token_for_request_user(request)
        self.message_user(
            request,
            _("Bearer token created. Copy it now — it will not be shown again: %(token)s")
            % {"token": raw_key},
            level=messages.SUCCESS,
        )

        if self.has_view_or_change_permission(request):
            return redirect("admin:actions_remoteactiontoken_changelist")
        if self.has_add_permission(request):
            return redirect("admin:actions_remoteactiontoken_add")
        return redirect("admin:index")

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

"""Admin integration for API explorer and service token management."""

from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone

from apps.apis.models import (
    APIExplorer,
    ResourceMethod,
    ServiceToken,
    ServiceTokenEvent,
)


class ResourceMethodInline(admin.TabularInline):
    """Inline editor for API resource methods."""

    model = ResourceMethod
    extra = 0
    fields = ("operation_name", "resource_path", "http_method")
    show_change_link = True


class ServiceTokenCreateForm(forms.Form):
    """Create form with policy-aware expiry and scoped permissions."""

    name = forms.CharField(max_length=120)
    scopes = forms.CharField(
        help_text="Comma-separated scope list (for example: ocpp.read,ocpp.write).",
    )
    expires_in_days = forms.IntegerField(
        min_value=1,
        max_value=ServiceToken.MAX_EXPIRY_DAYS,
        initial=30,
        help_text=f"Maximum allowed: {ServiceToken.MAX_EXPIRY_DAYS} days.",
    )

    def cleaned_scopes(self) -> list[str]:
        value = self.cleaned_data.get("scopes") or ""
        return sorted({scope.strip() for scope in value.split(",") if scope.strip()})


class ServiceTokenConfirmForm(forms.Form):
    """Confirmation form used by revoke and rotate flows."""

    impact_note = forms.CharField(
        required=False,
        max_length=300,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Document expected impact before changing token status.",
    )
    reason = forms.CharField(max_length=300)


@admin.register(APIExplorer)
class APIExplorerAdmin(admin.ModelAdmin):
    """Admin settings for API entry points."""

    list_display = ("name", "base_url", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "base_url", "description")
    inlines = (ResourceMethodInline,)


@admin.register(ResourceMethod)
class ResourceMethodAdmin(admin.ModelAdmin):
    """Admin settings for individual API resource methods."""

    list_display = ("operation_name", "api", "http_method", "resource_path", "updated_at")
    list_filter = ("http_method", "api")
    search_fields = ("operation_name", "resource_path", "api__name", "notes")


@admin.register(ServiceToken)
class ServiceTokenAdmin(admin.ModelAdmin):
    """Self-service workflow for scoped token request, reveal, revoke, and rotate."""

    list_display = ("name", "token_prefix", "status", "expires_at", "created_by", "created_at")
    list_filter = ("status", "created_at")
    readonly_fields = ("token_prefix", "secret_hash", "created_by", "created_at", "updated_at")
    search_fields = ("name", "token_prefix", "created_by__username")
    change_list_template = "admin/apis/servicetoken/change_list.html"

    def _require_manage_permission(self, request: HttpRequest) -> None:
        if not request.user.has_perm("apis.manage_service_tokens"):
            raise PermissionDenied

    def _require_reveal_permission(self, request: HttpRequest) -> None:
        if not request.user.has_perm("apis.reveal_service_token_secret"):
            raise PermissionDenied

    def get_urls(self):
        urls = super().get_urls()
        opts = self.model._meta
        custom = [
            path("create/", self.admin_site.admin_view(self.create_token), name=f"{opts.app_label}_{opts.model_name}_create"),
            path("<int:token_id>/reveal/", self.admin_site.admin_view(self.reveal_token), name=f"{opts.app_label}_{opts.model_name}_reveal"),
            path("<int:token_id>/revoke/", self.admin_site.admin_view(self.revoke_token), name=f"{opts.app_label}_{opts.model_name}_revoke"),
            path("<int:token_id>/rotate/", self.admin_site.admin_view(self.rotate_token), name=f"{opts.app_label}_{opts.model_name}_rotate"),
        ]
        return custom + urls

    def changelist_view(self, request: HttpRequest, extra_context=None):
        context = extra_context or {}
        context["create_url"] = reverse("admin:apis_servicetoken_create")
        return super().changelist_view(request, extra_context=context)

    def create_token(self, request: HttpRequest) -> HttpResponse:
        self._require_manage_permission(request)
        form = ServiceTokenCreateForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            expires_at = timezone.now() + timedelta(days=form.cleaned_data["expires_in_days"])
            token, raw_secret = ServiceToken.issue(
                actor=request.user,
                name=form.cleaned_data["name"],
                scopes=form.cleaned_scopes(),
                expires_at=expires_at,
            )
            request.session[f"service-token-secret:{token.pk}"] = raw_secret
            return HttpResponseRedirect(reverse("admin:apis_servicetoken_reveal", args=[token.pk]))
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Create service token",
            "form": form,
        }
        return TemplateResponse(request, "admin/apis/servicetoken/create.html", context)

    def reveal_token(self, request: HttpRequest, token_id: int) -> HttpResponse:
        self._require_reveal_permission(request)
        token = self.get_object(request, token_id)
        if token is None:
            raise PermissionDenied
        session_key = f"service-token-secret:{token.pk}"
        secret = request.session.pop(session_key, None)
        if secret:
            ServiceTokenEvent.record(
                token=token,
                event_type=ServiceTokenEvent.EventType.REVEALED,
                actor=request.user,
                details={"message": "Secret displayed once via admin reveal flow."},
            )
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Service token secret",
            "token": token,
            "secret": secret,
        }
        return TemplateResponse(request, "admin/apis/servicetoken/reveal.html", context)

    def revoke_token(self, request: HttpRequest, token_id: int) -> HttpResponse:
        self._require_manage_permission(request)
        token = self.get_object(request, token_id)
        if token is None:
            raise PermissionDenied
        form = ServiceTokenConfirmForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            token.revoke(
                actor=request.user,
                reason=form.cleaned_data["reason"],
                impact_note=form.cleaned_data["impact_note"],
            )
            messages.success(request, f"Revoked {token.name}.")
            return HttpResponseRedirect(reverse("admin:apis_servicetoken_changelist"))
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": f"Revoke {token.name}",
            "token": token,
            "action_name": "Revoke token",
            "impact_note": "Impact: integrations using this token lose access immediately.",
            "form": form,
        }
        return TemplateResponse(request, "admin/apis/servicetoken/confirm_action.html", context)

    def rotate_token(self, request: HttpRequest, token_id: int) -> HttpResponse:
        self._require_manage_permission(request)
        token = self.get_object(request, token_id)
        if token is None:
            raise PermissionDenied
        form = ServiceTokenConfirmForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            replacement, raw_secret = ServiceToken.issue(
                actor=request.user,
                name=f"{token.name} (rotated)",
                scopes=token.scopes,
                expires_at=token.expires_at,
                rotated_from=token,
            )
            token.status = ServiceToken.Status.REPLACED
            token.revoked_at = timezone.now()
            token.revoked_reason = form.cleaned_data["reason"]
            token.save(update_fields=["status", "revoked_at", "revoked_reason", "updated_at"])
            ServiceTokenEvent.record(
                token=token,
                event_type=ServiceTokenEvent.EventType.ROTATED,
                actor=request.user,
                details={
                    "replacement_id": replacement.pk,
                    "reason": form.cleaned_data["reason"],
                    "impact_note": form.cleaned_data["impact_note"],
                },
            )
            request.session[f"service-token-secret:{replacement.pk}"] = raw_secret
            return HttpResponseRedirect(reverse("admin:apis_servicetoken_reveal", args=[replacement.pk]))
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": f"Rotate {token.name}",
            "token": token,
            "action_name": "Rotate token",
            "impact_note": "Impact: current token becomes inactive and clients must switch to the new secret.",
            "form": form,
        }
        return TemplateResponse(request, "admin/apis/servicetoken/confirm_action.html", context)


@admin.register(ServiceTokenEvent)
class ServiceTokenEventAdmin(admin.ModelAdmin):
    """Read-only audit trail view for token lifecycle events."""

    list_display = ("token", "event_type", "actor", "created_at")
    list_filter = ("event_type", "created_at")
    readonly_fields = ("token", "event_type", "actor", "details", "created_at")
    search_fields = ("token__name", "actor__username", "token__token_prefix")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

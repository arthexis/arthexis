"""Admin integration for API explorer and service token management."""

from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone

from apps.apis.models import (
    APIExplorer,
    GeneralServiceToken,
    GeneralServiceTokenEvent,
    ResourceMethod,
    ServiceToken,
    ServiceTokenEvent,
)
from apps.groups.models import SecurityGroup


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


class GeneralServiceTokenCreateForm(forms.Form):
    """Wizard form to create manual JWT tokens for a target user."""

    name = forms.CharField(max_length=120)
    user_id = forms.IntegerField(min_value=1, help_text="User id that owns the token access scope.")
    expires_in_days = forms.IntegerField(
        min_value=1,
        max_value=GeneralServiceToken.MAX_EXPIRY_DAYS,
        initial=30,
        help_text=f"Maximum allowed: {GeneralServiceToken.MAX_EXPIRY_DAYS} days.",
    )
    security_group_ids = forms.CharField(
        required=False,
        help_text="Optional comma-separated Security Group ids. Empty means all user groups.",
    )
    custom_claims = forms.JSONField(
        required=False,
        initial=dict,
        help_text="Optional JSON object merged into JWT payload.",
    )

    def clean(self):
        cleaned = super().clean()
        user_model = get_user_model()
        user_id = cleaned.get("user_id")
        if user_id:
            user = user_model.objects.filter(pk=user_id).first()
            if user is None:
                self.add_error("user_id", "User not found.")
            else:
                cleaned["user"] = user
        group_ids_raw = cleaned.get("security_group_ids") or ""
        try:
            group_ids = sorted({int(part.strip()) for part in group_ids_raw.split(",") if part.strip()})
        except ValueError:
            self.add_error(
                "security_group_ids",
                "Security Group ids must be a comma-separated list of integers.",
            )
            group_ids = []
        if group_ids:
            groups = list(SecurityGroup.objects.filter(id__in=group_ids))
            found_ids = {group.id for group in groups}
            missing = [group_id for group_id in group_ids if group_id not in found_ids]
            if missing:
                self.add_error("security_group_ids", f"Unknown Security Group ids: {missing}")
            if "user" in cleaned:
                user_group_ids = set(cleaned["user"].groups.values_list("id", flat=True))
                invalid = [group_id for group_id in group_ids if group_id not in user_group_ids]
                if invalid:
                    self.add_error(
                        "security_group_ids",
                        f"User lacks access to Security Group ids: {invalid}",
                    )
            cleaned["security_groups"] = groups
        else:
            cleaned["security_groups"] = []
        return cleaned


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
    readonly_fields = (
        "created_at",
        "created_by",
        "expires_at",
        "name",
        "revoked_at",
        "revoked_reason",
        "rotated_from",
        "scopes",
        "secret_hash",
        "status",
        "token_prefix",
        "updated_at",
    )
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

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def changeform_view(self, request: HttpRequest, object_id=None, form_url="", extra_context=None):
        if request.method == "POST":
            raise PermissionDenied
        return super().changeform_view(
            request,
            object_id=object_id,
            form_url=form_url,
            extra_context=extra_context,
        )

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
            if token.is_expired:
                form.add_error(
                    None,
                    "Cannot rotate an expired token. Issue a new token with a future expiry.",
                )
            else:
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


@admin.register(GeneralServiceToken)
class GeneralServiceTokenAdmin(admin.ModelAdmin):
    """Admin wizard for general JWT token issue/reveal/revoke and automatic retirement."""

    list_display = ("name", "user", "token_prefix", "status", "expires_at", "created_by", "created_at")
    list_filter = ("status", "created_at")
    readonly_fields = (
        "claims",
        "created_at",
        "created_by",
        "expires_at",
        "name",
        "retired_at",
        "revoked_at",
        "revoked_reason",
        "security_groups",
        "status",
        "token_hash",
        "token_prefix",
        "updated_at",
        "user",
    )
    search_fields = ("name", "token_prefix", "user__username", "created_by__username")
    change_list_template = "admin/apis/generaltoken/change_list.html"

    def _require_manage_permission(self, request: HttpRequest) -> None:
        if not request.user.has_perm("apis.manage_general_service_tokens"):
            raise PermissionDenied

    def _require_reveal_permission(self, request: HttpRequest) -> None:
        if not request.user.has_perm("apis.reveal_general_service_token_secret"):
            raise PermissionDenied

    def get_urls(self):
        urls = super().get_urls()
        opts = self.model._meta
        custom = [
            path("create/", self.admin_site.admin_view(self.create_token), name=f"{opts.app_label}_{opts.model_name}_create"),
            path("<int:token_id>/reveal/", self.admin_site.admin_view(self.reveal_token), name=f"{opts.app_label}_{opts.model_name}_reveal"),
            path("<int:token_id>/revoke/", self.admin_site.admin_view(self.revoke_token), name=f"{opts.app_label}_{opts.model_name}_revoke"),
        ]
        return custom + urls

    def changelist_view(self, request: HttpRequest, extra_context=None):
        GeneralServiceToken.retire_expired_tokens()
        context = extra_context or {}
        context["create_url"] = reverse("admin:apis_generalservicetoken_create")
        return super().changelist_view(request, extra_context=context)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def changeform_view(self, request: HttpRequest, object_id=None, form_url="", extra_context=None):
        if request.method == "POST":
            raise PermissionDenied
        return super().changeform_view(
            request,
            object_id=object_id,
            form_url=form_url,
            extra_context=extra_context,
        )

    def create_token(self, request: HttpRequest) -> HttpResponse:
        self._require_manage_permission(request)
        form = GeneralServiceTokenCreateForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            expires_at = timezone.now() + timedelta(days=form.cleaned_data["expires_in_days"])
            token, raw_token = GeneralServiceToken.issue(
                actor=request.user,
                user=form.cleaned_data["user"],
                name=form.cleaned_data["name"],
                expires_at=expires_at,
                security_groups=form.cleaned_data["security_groups"],
                claims=form.cleaned_data.get("custom_claims") or {},
            )
            request.session[f"general-service-token-secret:{token.pk}"] = raw_token
            return HttpResponseRedirect(reverse("admin:apis_generalservicetoken_reveal", args=[token.pk]))
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Create general service token",
            "form": form,
        }
        return TemplateResponse(request, "admin/apis/generaltoken/create.html", context)

    def reveal_token(self, request: HttpRequest, token_id: int) -> HttpResponse:
        self._require_reveal_permission(request)
        token = self.get_object(request, token_id)
        if token is None:
            raise PermissionDenied
        session_key = f"general-service-token-secret:{token.pk}"
        secret = request.session.pop(session_key, None)
        if secret:
            GeneralServiceTokenEvent.record(
                token=token,
                event_type=GeneralServiceTokenEvent.EventType.REVEALED,
                actor=request.user,
                details={"message": "Secret displayed once via admin reveal flow."},
            )
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "General service token secret",
            "token": token,
            "secret": secret,
        }
        return TemplateResponse(request, "admin/apis/generaltoken/reveal.html", context)

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
            return HttpResponseRedirect(reverse("admin:apis_generalservicetoken_changelist"))
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


@admin.register(GeneralServiceTokenEvent)
class GeneralServiceTokenEventAdmin(admin.ModelAdmin):
    """Read-only audit trail view for general service token lifecycle events."""

    list_display = ("token", "event_type", "actor", "created_at")
    list_filter = ("event_type", "created_at")
    readonly_fields = ("token", "event_type", "actor", "details", "created_at")
    search_fields = ("token__name", "actor__username", "token__token_prefix")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

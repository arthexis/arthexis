"""Admin registrations for the users app."""

from __future__ import annotations

import json

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.forms import ModelForm
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import InvalidJSONStructure, InvalidRegistrationResponse

from apps.core.admin.mixins import OwnableAdminForm, OwnableAdminMixin

from .diagnostics import build_diagnostic_bundle, create_manual_feedback
from .models import (
    ChatProfile,
    PasskeyCredential,
    User,
    UserDiagnosticBundle,
    UserDiagnosticEvent,
    UploadedErrorReport,
    UserDiagnosticsProfile,
    UserFlag,
)
from .passkeys import build_registration_options, verify_registration_response
from apps.celery.utils import enqueue_task

from .tasks import analyze_uploaded_error_report

PASSKEY_REGISTRATION_SESSION_KEY = "users_admin_passkey_registration"


class ChatProfileAdminForm(OwnableAdminForm):
    """Admin form that supports avatar ownership alongside user/group owners."""

    def clean(self):
        cleaned_data = ModelForm.clean(self)

        user = cleaned_data.get("user")
        group = cleaned_data.get("group")
        avatar = cleaned_data.get("avatar")
        owner_count = sum(bool(owner) for owner in (user, group, avatar))

        if owner_count > 1:
            raise ValidationError(
                _("A chat profile must have exactly one owner (user, group, or avatar).")
            )

        owner_required = getattr(self._meta.model, "owner_required", True)
        if owner_required and owner_count == 0:
            raise ValidationError(
                _("A chat profile must be assigned to a user, group, or avatar.")
            )

        return cleaned_data


class PasskeyRegistrationForm(forms.Form):
    """Collect user and friendly label before creating WebAuthn options."""

    user = forms.ModelChoiceField(queryset=User.objects.all())
    name = forms.CharField(max_length=80)


@admin.register(ChatProfile)
class ChatProfileAdmin(OwnableAdminMixin, admin.ModelAdmin):
    """Manage per-owner chat preferences."""

    form = ChatProfileAdminForm

    list_display = (
        "id",
        "owner_display",
        "contact_via_chat",
        "is_enabled",
    )
    list_filter = ("contact_via_chat", "is_enabled")
    search_fields = ("user__username", "group__name", "avatar__name")


@admin.register(PasskeyCredential)
class PasskeyCredentialAdmin(admin.ModelAdmin):
    """Manage enrolled passkeys and provide a browser-assisted registration wizard."""

    list_display = ("name", "user", "last_used_at", "created_at")
    search_fields = ("name", "user__username", "user__email", "credential_id")
    readonly_fields = ("credential_id", "created_at", "last_used_at", "sign_count", "updated_at")

    change_list_template = "admin/users/passkeycredential_changelist.html"

    def get_urls(self):
        custom = [
            path(
                "register/",
                self.admin_site.admin_view(self.registration_wizard_view),
                name="users_passkeycredential_register",
            ),
        ]
        return custom + super().get_urls()

    def registration_wizard_view(self, request: HttpRequest) -> HttpResponse:
        if not self.has_add_permission(request):
            messages.error(request, _("You do not have permission to register passkeys."))
            return redirect(reverse("admin:index"))

        form = PasskeyRegistrationForm(request.POST or None)
        options_data = None

        if request.method == "POST" and "start" in request.POST:
            if form.is_valid():
                user = form.cleaned_data["user"]
                name = form.cleaned_data["name"].strip()
                options = build_registration_options(
                    request,
                    user_id=str(user.pk).encode("utf-8"),
                    user_name=user.get_username(),
                    user_display_name=user.get_full_name() or user.get_username(),
                    rp_name=self.admin_site.site_header,
                    exclude_credentials=(
                        base64url_to_bytes(credential.credential_id)
                        for credential in user.passkeys.only("credential_id")
                    ),
                )
                request.session[PASSKEY_REGISTRATION_SESSION_KEY] = {
                    "challenge": options.challenge,
                    "name": name,
                    "user_handle": options.user_handle,
                    "user_id": user.pk,
                }
                options_data = options.data

        if request.method == "POST" and "finish" in request.POST:
            pending = request.session.get(PASSKEY_REGISTRATION_SESSION_KEY) or {}
            challenge = pending.get("challenge")
            user_id = pending.get("user_id")
            name = pending.get("name")
            user_handle = pending.get("user_handle")
            if not all((challenge, user_id, name, user_handle)):
                messages.error(request, _("Passkey registration session expired. Please restart."))
                return redirect(reverse("admin:users_passkeycredential_register"))

            user = User.objects.filter(pk=user_id).first()
            if user is None:
                messages.error(request, _("Selected user was not found."))
                request.session.pop(PASSKEY_REGISTRATION_SESSION_KEY, None)
                return redirect(reverse("admin:users_passkeycredential_register"))

            try:
                credential = json.loads(request.POST.get("credential_json") or "")
                verified = verify_registration_response(
                    request,
                    credential,
                    expected_challenge=challenge,
                )
            except (TypeError, ValueError, InvalidJSONStructure, InvalidRegistrationResponse):
                messages.error(request, _("Passkey verification failed. Please try again."))
            else:
                transports = credential.get("response", {}).get("transports") or []
                try:
                    passkey = PasskeyCredential.objects.create(
                        user=user,
                        name=name,
                        credential_id=bytes_to_base64url(verified.credential_id),
                        public_key=verified.credential_public_key,
                        sign_count=verified.sign_count,
                        user_handle=user_handle,
                        transports=transports,
                    )
                except IntegrityError:
                    messages.error(
                        request,
                        _("A passkey with this name or credential already exists for the user."),
                    )
                else:
                    request.session.pop(PASSKEY_REGISTRATION_SESSION_KEY, None)
                    messages.success(request, _("Passkey registered successfully."))
                    return redirect(reverse("admin:users_passkeycredential_change", args=[passkey.pk]))

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Register passkey"),
            "form": form,
            "registration_url": reverse("admin:users_passkeycredential_register"),
            "public_key_options_data": options_data,
        }
        return TemplateResponse(request, "admin/users/passkeycredential_wizard.html", context)


@admin.register(UserFlag)
class UserFlagAdmin(admin.ModelAdmin):
    """Manage user-level flags that apply independently of avatars."""

    list_display = ("id", "user", "key", "is_enabled", "updated_at")
    list_filter = ("is_enabled",)
    search_fields = ("user__username", "user__email", "key")


@admin.register(UserDiagnosticsProfile)
class UserDiagnosticsProfileAdmin(OwnableAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "owner_display",
        "is_enabled",
        "collect_diagnostics",
        "allow_manual_feedback",
    )
    list_filter = ("is_enabled", "collect_diagnostics", "allow_manual_feedback")
    search_fields = ("user__username", "group__name", "avatar__name")


@admin.register(UserDiagnosticEvent)
class UserDiagnosticEventAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "source", "summary", "request_method", "occurred_at")
    list_filter = ("source", "occurred_at")
    search_fields = ("user__username", "summary", "details", "request_path", "fingerprint")
    readonly_fields = ("fingerprint", "occurred_at", "metadata")
    actions = ("create_bundle_for_selected_users",)

    @admin.action(description=_("Create diagnostics bundle for selected users"))
    def create_bundle_for_selected_users(self, request, queryset):
        user_ids = sorted(set(queryset.values_list("user_id", flat=True)))
        created = 0
        for user_id in user_ids:
            if not user_id:
                continue
            user = User.objects.filter(pk=user_id).first()
            if user is None:
                continue
            build_diagnostic_bundle(user=user)
            created += 1
        if created:
            self.message_user(
                request,
                _("Created %(count)s diagnostics bundle(s).") % {"count": created},
                level=messages.SUCCESS,
            )
            return
        self.message_user(request, _("No bundles were created."), level=messages.WARNING)

    def save_model(self, request, obj, form, change):
        if not change and obj.source == UserDiagnosticEvent.Source.FEEDBACK:
            event = create_manual_feedback(
                user=obj.user,
                summary=obj.summary,
                details=obj.details,
            )
            if event is None:
                self.message_user(
                    request,
                    _(
                        "Manual feedback is disabled for this user profile or no diagnostics profile exists."
                    ),
                    level=messages.ERROR,
                )
                raise ValidationError(
                    _(
                        "Cannot save feedback: manual feedback is disabled for this user."
                    )
                )
            obj.pk = event.pk
            return
        super().save_model(request, obj, form, change)


@admin.register(UserDiagnosticBundle)
class UserDiagnosticBundleAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "user", "created_at")
    list_filter = ("created_at",)
    search_fields = ("title", "user__username", "report")
    filter_horizontal = ("events",)
    readonly_fields = ("created_at",)


@admin.register(UploadedErrorReport)
class UploadedErrorReportAdmin(admin.ModelAdmin):
    list_display = ("id", "source_label", "uploaded_by", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("source_label", "package")
    readonly_fields = ("analysis", "error", "status", "created_at", "updated_at")

    change_list_template = "admin/users/uploaded_error_report_changelist.html"
    change_form_template = "admin/users/uploaded_error_report_change_form.html"

    def get_urls(self):
        custom = [
            path("upload/", self.admin_site.admin_view(self.upload_view), name="users_uploadederrorreport_upload"),
        ]
        return custom + super().get_urls()

    def upload_view(self, request: HttpRequest) -> HttpResponse:
        if request.method == "POST":
            uploaded = request.FILES.get("package")
            if not uploaded:
                messages.error(request, _("Choose a .zip package to upload."))
                return redirect("admin:users_uploadederrorreport_upload")
            report = UploadedErrorReport.objects.create(
                source_label=(request.POST.get("source_label") or "").strip(),
                uploaded_by=request.user if request.user.is_authenticated else None,
                package=uploaded,
            )
            if not enqueue_task(analyze_uploaded_error_report, report.pk, require_enabled=False):
                analyze_uploaded_error_report(report.pk)
            return redirect(reverse("admin:users_uploadederrorreport_change", args=[report.pk]))

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Upload error report"),
            "refresh_ms": 3000,
        }
        return TemplateResponse(request, "admin/users/uploaded_error_report_upload.html", context)


__all__ = ["admin"]

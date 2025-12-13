"""Admin configuration and QR enrollment wizard for TOTP devices."""

from __future__ import annotations

from typing import Optional

from django import forms
from django.contrib import admin, messages
from django.db import IntegrityError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from django.template.response import TemplateResponse

from django_object_actions import DjangoObjectActions

from apps.users.models import User

from .models import TOTPDevice


class TOTPDeviceRegistrationForm(forms.ModelForm):
    class Meta:
        model = TOTPDevice
        fields = ("user", "name")
        widgets = {
            "name": forms.TextInput(attrs={"maxlength": 64}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = User.objects.all()
        self.fields["name"].initial = _("Authenticator")


class TOTPConfirmationForm(forms.Form):
    token = forms.CharField(
        label=_("One-time code"),
        max_length=8,
        min_length=6,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
            }
        ),
    )


@admin.register(TOTPDevice)
class TOTPDeviceAdmin(DjangoObjectActions, admin.ModelAdmin):
    list_display = ("name", "user", "confirmed", "last_used_at")
    list_filter = ("confirmed",)
    search_fields = ("name", "user__username", "user__email")
    readonly_fields = ("last_used_at", "provisioning_uri")
    fields = (
        "user",
        "name",
        "confirmed",
        "provisioning_uri",
        "last_used_at",
    )

    change_list_template = "admin/totp/device_changelist.html"

    changelist_actions = ["registration_wizard"]

    def get_urls(self):
        custom = [
            path(
                "register/",
                self.admin_site.admin_view(self.registration_wizard_view),
                name="totp_totpdevice_register",
            ),
        ]
        return custom + super().get_urls()

    def registration_wizard(self, request, queryset=None):
        return HttpResponseRedirect(reverse("admin:totp_totpdevice_register"))

    registration_wizard.label = _("Register via QR")
    registration_wizard.short_description = _("Register via QR")
    registration_wizard.changelist = True

    def registration_wizard_view(self, request: HttpRequest) -> HttpResponse:
        if not self.has_add_permission(request):
            return self._unauthorized(request)

        device = self._get_requested_device(request)
        setup_form = TOTPDeviceRegistrationForm(
            request.POST or None, instance=device if device else None
        )
        confirm_form = None
        qr_data_uri: Optional[str] = None
        manual_key: Optional[str] = None

        if request.method == "POST" and "start" in request.POST:
            if setup_form.is_valid():
                device = setup_form.save(commit=False)
                device.key = TOTPDevice.generate_key()
                if not device.name:
                    device.name = TOTPDevice.generate_name(device.user)
                device.confirmed = False
                try:
                    device.save()
                except IntegrityError:
                    setup_form.add_error(
                        "name", _("A device with this name already exists for the user."),
                    )
                else:
                    messages.success(
                        request, _("Authenticator secret generated. Scan to continue."),
                    )
                    return redirect(
                        reverse("admin:totp_totpdevice_register") + f"?device={device.pk}"
                    )
        elif request.method == "POST" and device and "confirm" in request.POST:
            if not self.has_change_permission(request, device):
                return self._unauthorized(request)
            confirm_form = TOTPConfirmationForm(request.POST)
            if confirm_form.is_valid():
                if device.verify_token(confirm_form.cleaned_data["token"]):
                    if not device.confirmed:
                        device.confirmed = True
                        device.save(update_fields=["confirmed", "last_t", "drift", "last_used_at", "throttling_failure_count", "throttling_failure_timestamp"])
                    messages.success(request, _("TOTP device confirmed and ready to use."))
                    return redirect(
                        reverse("admin:totp_totpdevice_change", args=[device.pk])
                    )
                confirm_form.add_error("token", _("Invalid or expired code."))
        elif request.method == "POST" and device and "cancel" in request.POST:
            device.delete()
            messages.info(request, _("Pending TOTP device removed."))
            return redirect(reverse("admin:totp_totpdevice_changelist"))
        else:
            confirm_form = TOTPConfirmationForm()

        if device:
            qr_data_uri = device.render_qr_data_uri()
            manual_key = device.base32_key
            if not confirm_form:
                confirm_form = TOTPConfirmationForm()

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "has_view_permission": self.has_view_permission(request),
            "title": _("Register authenticator app"),
            "setup_form": setup_form,
            "confirm_form": confirm_form,
            "device": device,
            "qr_data_uri": qr_data_uri,
            "manual_key": manual_key,
            "breadcrumbs_title": _("Register authenticator app"),
            "registration_url": reverse("admin:totp_totpdevice_register"),
        }
        return TemplateResponse(request, "admin/totp/device_wizard.html", context)

    def _get_requested_device(self, request: HttpRequest) -> Optional[TOTPDevice]:
        device_id = request.GET.get("device") or request.POST.get("device")
        if not device_id:
            return None
        try:
            device = TOTPDevice.objects.get(pk=device_id)
        except TOTPDevice.DoesNotExist:
            return None
        if not self.has_change_permission(request, device):
            return None
        return device

    def _unauthorized(self, request: HttpRequest) -> HttpResponse:
        messages.error(request, _("You do not have permission to register devices."))
        return redirect(reverse("admin:index"))

    def has_add_permission(self, request):
        return request.user.has_perm("totp.add_totpdevice")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("totp.change_totpdevice")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("totp.delete_totpdevice")

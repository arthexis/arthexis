"""Forms for the pages app."""

from __future__ import annotations

from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_variables

from core.form_fields import Base64FileField

from .models import UserManual, UserStory


class AuthenticatorLoginForm(AuthenticationForm):
    """Authentication form that supports password or authenticator codes."""

    otp_token = forms.CharField(
        label=_("Authenticator code"),
        required=False,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
            }
        ),
    )
    auth_method = forms.CharField(required=False, widget=forms.HiddenInput(), initial="password")

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_token": _("The authenticator code is invalid or has expired."),
        "token_required": _("Enter the code from your authenticator app."),
        "password_required": _("Enter your password."),
    }

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request=request, *args, **kwargs)
        self.fields["password"].required = False
        self.fields["otp_token"].strip = True
        self.fields["auth_method"].initial = "password"
        self.verified_device = None

    def get_invalid_token_error(self) -> ValidationError:
        return ValidationError(self.error_messages["invalid_token"], code="invalid_token")

    def get_token_required_error(self) -> ValidationError:
        return ValidationError(self.error_messages["token_required"], code="token_required")

    def get_password_required_error(self) -> ValidationError:
        return ValidationError(self.error_messages["password_required"], code="password_required")

    @sensitive_variables()
    def clean(self):
        username = self.cleaned_data.get("username")
        method = (self.cleaned_data.get("auth_method") or "password").lower()
        if method not in {"password", "otp"}:
            method = "password"
        self.cleaned_data["auth_method"] = method

        if username is not None:
            if method == "otp":
                token = (self.cleaned_data.get("otp_token") or "").strip().replace(" ", "")
                if not token:
                    raise self.get_token_required_error()
                self.user_cache = authenticate(
                    self.request,
                    username=username,
                    otp_token=token,
                )
                if self.user_cache is None:
                    raise self.get_invalid_token_error()
                self.cleaned_data["otp_token"] = token
                self.verified_device = getattr(self.user_cache, "otp_device", None)
            else:
                password = self.cleaned_data.get("password")
                if not password:
                    raise self.get_password_required_error()
                self.user_cache = authenticate(
                    self.request, username=username, password=password
                )
                if self.user_cache is None:
                    raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data

    def get_verified_device(self):
        return self.verified_device


class AuthenticatorEnrollmentForm(forms.Form):
    """Form used to confirm a pending authenticator enrollment."""

    token = forms.CharField(
        label=_("Authenticator code"),
        min_length=6,
        max_length=8,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
            }
        ),
    )

    error_messages = {
        "invalid_token": _("The provided code is invalid or has expired."),
        "missing_device": _("Generate a new authenticator secret before confirming it."),
    }

    def __init__(self, *args, device=None, **kwargs):
        self.device = device
        super().__init__(*args, **kwargs)

    def clean_token(self):
        token = (self.cleaned_data.get("token") or "").strip().replace(" ", "")
        if not token:
            raise forms.ValidationError(self.error_messages["invalid_token"], code="invalid_token")
        if self.device is None:
            raise forms.ValidationError(self.error_messages["missing_device"], code="missing_device")
        try:
            verified = self.device.verify_token(token)
        except Exception:
            verified = False
        if not verified:
            raise forms.ValidationError(self.error_messages["invalid_token"], code="invalid_token")
        return token

    def get_verified_device(self):
        return self.device


_manual_pdf_field = UserManual._meta.get_field("content_pdf")


class UserManualAdminForm(forms.ModelForm):
    content_pdf = Base64FileField(
        label=_manual_pdf_field.verbose_name,
        help_text=_manual_pdf_field.help_text,
        required=not _manual_pdf_field.blank,
        content_type="application/pdf",
        download_name="manual.pdf",
    )

    class Meta:
        model = UserManual
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = getattr(self, "instance", None)
        slug = getattr(instance, "slug", "")
        if slug:
            self.fields["content_pdf"].widget.download_name = f"{slug}.pdf"
        self.fields["content_pdf"].widget.attrs.setdefault(
            "accept", "application/pdf"
        )


class UserStoryForm(forms.ModelForm):
    class Meta:
        model = UserStory
        fields = ("name", "rating", "comments", "take_screenshot", "path")
        widgets = {
            "path": forms.HiddenInput(),
            "comments": forms.Textarea(attrs={"rows": 4, "maxlength": 400}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        if user is not None and user.is_authenticated:
            name_field = self.fields["name"]
            name_field.required = False
            name_field.label = _("Username")
            name_field.initial = (user.get_username() or "")[:40]
            name_field.widget.attrs.update(
                {
                    "maxlength": 40,
                    "readonly": "readonly",
                }
            )
        else:
            self.fields["name"] = forms.EmailField(
                label=_("Email address"),
                max_length=40,
                required=True,
                widget=forms.EmailInput(
                    attrs={
                        "maxlength": 40,
                        "placeholder": _("name@example.com"),
                        "autocomplete": "email",
                        "inputmode": "email",
                    }
                ),
            )
        self.fields["take_screenshot"].initial = True
        self.fields["rating"].widget = forms.RadioSelect(
            choices=[(i, str(i)) for i in range(1, 6)]
        )

    def clean_comments(self):
        comments = (self.cleaned_data.get("comments") or "").strip()
        if len(comments) > 400:
            raise forms.ValidationError(
                _("Feedback must be 400 characters or fewer."), code="max_length"
            )
        return comments

    def clean_name(self):
        if self.user is not None and self.user.is_authenticated:
            return (self.user.get_username() or "")[:40]

        name = (self.cleaned_data.get("name") or "").strip()
        return name[:40]

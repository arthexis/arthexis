"""Forms for the pages app."""

from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_variables

from .models import UserStory


class AuthenticatorLoginForm(AuthenticationForm):
    """Authentication form that relies solely on username and password."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "password_required": _("Enter your password or one-time code."),
    }

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request=request, *args, **kwargs)
        self.verified_device = None

    def get_password_required_error(self) -> ValidationError:
        return ValidationError(self.error_messages["password_required"], code="password_required")

    @sensitive_variables()
    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")

        if username and not password:
            raise self.get_password_required_error()

        cleaned = super().clean()
        self.user_cache = getattr(self, "user_cache", None)
        return cleaned

    def get_verified_device(self):
        return None


class UserStoryForm(forms.ModelForm):
    class Meta:
        model = UserStory
        fields = ("name", "rating", "comments", "path")
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
        self.fields["rating"].widget = forms.RadioSelect(
            choices=[(i, str(i)) for i in range(1, 6)]
        )

    def clean_comments(self):
        comments = (self.cleaned_data.get("comments") or "").strip()
        if len(comments) > 400:
            raise forms.ValidationError(
                _("Please keep your comment under 400 characters."), code="too_long"
            )
        return comments

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if len(name) > 40:
            raise forms.ValidationError(
                _("Names must be 40 characters or fewer."), code="too_long"
            )
        return name

    def clean_path(self):
        return (self.cleaned_data.get("path") or "").strip()

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.user is not None and self.user.is_authenticated:
            instance.user = self.user
        if commit:
            instance.save()
        return instance

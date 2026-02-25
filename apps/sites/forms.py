"""Forms for the pages app."""

from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
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


class MultipleFileInput(forms.ClearableFileInput):
    """Widget that allows selecting multiple files for a single field."""

    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """File field that validates each uploaded file in a multi-upload payload."""

    widget = MultipleFileInput

    def clean(self, data, initial=None):
        single_clean = super().clean
        if not data:
            return []
        if isinstance(data, (list, tuple)):
            return [single_clean(item, initial) for item in data]
        return [single_clean(data, initial)]


class UserStoryForm(forms.ModelForm):
    """Feedback form for user stories, including optional authenticated attachments."""

    MAX_NON_STAFF_ATTACHMENTS = 3

    class Meta:
        model = UserStory
        fields = ("name", "rating", "comments", "path", "messages")
        widgets = {
            "path": forms.HiddenInput(),
            "comments": forms.Textarea(attrs={"rows": 4, "maxlength": 400}),
            "messages": forms.HiddenInput(),
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
        self.fields["attachments"] = MultipleFileField(
            required=False,
            label=_("Attachments"),
            help_text=_("Attach files to provide additional context."),
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

    def clean_messages(self):
        return (self.cleaned_data.get("messages") or "").strip()


    def clean_attachments(self) -> list[UploadedFile]:
        """Validate attachment permissions and count constraints by user role."""

        attachments = self.cleaned_data.get("attachments") or []
        if not attachments:
            return []

        if self.user is None or not self.user.is_authenticated:
            raise forms.ValidationError(
                _("Anonymous feedback cannot include file uploads."),
                code="anonymous_attachments_not_allowed",
            )

        if not self.user.is_staff and len(attachments) > self.MAX_NON_STAFF_ATTACHMENTS:
            raise forms.ValidationError(
                _("You can upload up to %(limit)s files.")
                % {"limit": self.MAX_NON_STAFF_ATTACHMENTS},
                code="too_many_attachments",
            )

        return attachments

    def get_cleaned_attachments(self) -> list[UploadedFile]:
        """Return validated uploaded attachment files."""

        return self.cleaned_data.get("attachments") or []

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.user is not None and self.user.is_authenticated:
            instance.user = self.user
        if commit:
            instance.save()
        return instance

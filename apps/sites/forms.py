"""Forms for the pages app."""

from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import MaxLengthValidator
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


class MultiFileInput(forms.ClearableFileInput):
    """File input widget that accepts multiple files."""

    allow_multiple_selected = True


class UserStoryForm(forms.ModelForm):
    """Form used to submit user-story feedback from public and admin pages."""

    non_staff_attachment_limit = 3
    attachments = forms.Field(required=False, widget=MultiFileInput(attrs={"multiple": True}))

    class Meta:
        model = UserStory
        fields = ("name", "rating", "comments", "path", "messages")
        widgets = {
            "path": forms.HiddenInput(),
            "comments": forms.Textarea(attrs={"rows": 4}),
            "messages": forms.HiddenInput(),
        }

    def __init__(self, *args, user=None, files=None, **kwargs):
        self.user = user
        super().__init__(*args, files=files, **kwargs)

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

        comments_field = self.fields["comments"]
        comments_widget = comments_field.widget
        if self._is_staff_user:
            comments_widget.attrs.pop("maxlength", None)
            comments_field.max_length = None
            comments_field.validators = [
                validator
                for validator in comments_field.validators
                if not (
                    isinstance(validator, MaxLengthValidator)
                    and getattr(validator, "limit_value", None) == 400
                )
            ]
        else:
            comments_widget.attrs["maxlength"] = 400

        attachments_field = self.fields["attachments"]
        if not self._is_authenticated_user:
            attachments_field.widget = forms.HiddenInput()
            attachments_field.disabled = True
            attachments_field.required = False
            attachments_field.help_text = _("Sign in to attach files.")
        elif self._is_staff_user:
            attachments_field.help_text = _("Attach one or more files.")
        else:
            attachments_field.help_text = _(
                "Attach up to %(limit)s files."
            ) % {"limit": self.non_staff_attachment_limit}

    @property
    def _is_authenticated_user(self) -> bool:
        return bool(self.user is not None and self.user.is_authenticated)

    @property
    def _is_staff_user(self) -> bool:
        return bool(self._is_authenticated_user and self.user.is_staff)


    def _post_clean(self):
        """Allow staff submissions to bypass model-level comment length validation."""

        super()._post_clean()
        if not self._is_staff_user:
            return

        comment_errors = self.errors.as_data().get("comments", [])
        if any(error.code == "max_length" for error in comment_errors):
            self.errors.pop("comments", None)

    def clean_comments(self):
        comments = (self.cleaned_data.get("comments") or "").strip()
        if not self._is_staff_user and len(comments) > 400:
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
        """Validate and return uploaded feedback attachments."""

        if not self._is_authenticated_user:
            return []

        files = self.files.getlist("attachments")
        if not files:
            return []

        if not self._is_staff_user and len(files) > self.non_staff_attachment_limit:
            raise forms.ValidationError(
                _("You can upload up to %(limit)s files.") % {
                    "limit": self.non_staff_attachment_limit,
                },
                code="too_many_files",
            )

        return files

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.user is not None and self.user.is_authenticated:
            instance.user = self.user
        if commit:
            instance.save()
        return instance

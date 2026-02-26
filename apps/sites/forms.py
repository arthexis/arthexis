"""Forms for the pages app."""

from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views.decorators.debug import sensitive_variables

from .models import UserStory, UserStoryAttachment


ANONYMOUS_ATTACHMENT_LIMIT = 0
AUTHENTICATED_ATTACHMENT_LIMIT = 3


class MultipleFileInput(forms.ClearableFileInput):
    """File input widget that supports selecting multiple files."""

    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """File field that returns a list when multiple files are submitted."""

    widget = MultipleFileInput

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(d, initial) for d in data]
        return single_clean(data, initial)


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
    """Feedback form used on public and admin pages."""

    attachments = MultipleFileField(required=False)

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
        self.upload_files = files.getlist("attachments") if files is not None else []
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
        self.fields["attachments"].label = _("Attachments")

    def get_comment_limit(self) -> int | None:
        """Return comment length limit for the current user, or None when unlimited."""

        if self.user is not None and self.user.is_authenticated and self.user.is_staff:
            return None
        return 400

    def get_attachment_limit(self) -> int | None:
        """Return maximum number of files allowed for the current user, or None if unlimited."""

        if self.user is None or not self.user.is_authenticated:
            return ANONYMOUS_ATTACHMENT_LIMIT
        if self.user.is_staff:
            return None
        return int(getattr(settings, "USER_STORY_ATTACHMENT_LIMIT", AUTHENTICATED_ATTACHMENT_LIMIT))

    def clean_comments(self):
        """Validate comments length according to role-based limits."""

        comments = (self.cleaned_data.get("comments") or "").strip()
        limit = self.get_comment_limit()
        if limit is not None and len(comments) > limit:
            raise forms.ValidationError(
                _("Please keep your comment under 400 characters."), code="too_long"
            )
        return comments

    def clean_attachments(self):
        """Validate role-based attachment permissions and limits."""

        limit = self.get_attachment_limit()
        attachment_count = len(self.upload_files)
        if limit == 0 and attachment_count:
            raise forms.ValidationError(_("File uploads are not available for your account."), code="forbidden")
        if limit is not None and limit > 0 and attachment_count > limit:
            raise forms.ValidationError(
                ngettext(
                    "You can upload up to %(count)s file.",
                    "You can upload up to %(count)s files.",
                    limit,
                )
                % {"count": limit},
                code="too_many_files",
            )
        return self.cleaned_data.get("attachments")

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

    def save(self, commit=True):
        """Persist feedback and any uploaded attachments."""

        instance = super().save(commit=False)
        if self.user is not None and self.user.is_authenticated:
            instance.user = self.user
        if commit:
            instance.save()
            self.save_attachments()
        return instance

    def save_attachments(self):
        """Persist uploaded attachments for the current instance once it exists."""

        if not self.instance.pk:
            return
        for uploaded_file in self.upload_files:
            UserStoryAttachment.objects.create(user_story=self.instance, file=uploaded_file)

"""Forms for operator journey guided actions."""

from __future__ import annotations

import secrets
import string

from django import forms
from django.contrib.auth import get_user_model

from apps.groups.models import SecurityGroup


class OperatorJourneyProvisionSuperuserForm(forms.Form):
    """Create a superuser account for operations onboarding."""

    PASSWORD_ALPHABET = string.ascii_letters + string.digits + "-._~!@#$%^&*"

    username = forms.CharField(
        max_length=150, help_text="Login name for the new superuser."
    )
    email = forms.EmailField(
        required=False, help_text="Optional email address for account recovery."
    )
    security_groups = forms.ModelMultipleChoiceField(
        queryset=SecurityGroup.objects.order_by("name"),
        required=True,
        help_text="Pick one or more security groups to assign.",
    )
    password_mode = forms.ChoiceField(
        choices=(
            ("random", "Generate random password"),
            ("custom", "Set password manually"),
        ),
        initial="random",
    )
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(),
        help_text="Required only when using a manual password.",
    )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("password_mode") == "custom" and not cleaned_data.get(
            "password"
        ):
            self.add_error(
                "password", "Provide a password or switch to random generation."
            )
        return cleaned_data

    def clean_username(self):
        """Reject usernames that already exist."""

        user_model = get_user_model()
        username = user_model.normalize_username(
            (self.cleaned_data.get("username") or "").strip()
        )
        user_manager = getattr(user_model, "all_objects", user_model._default_manager)
        if user_manager.filter(username=username).exists():
            raise forms.ValidationError("A user with this username already exists.")
        return username

    def save(self):
        """Create the superuser, assign groups, and return the account/password tuple."""

        cleaned_data = self.cleaned_data
        if cleaned_data["password_mode"] == "custom":
            password = cleaned_data["password"]
        else:
            password = self._generate_password()
        username = cleaned_data["username"]
        email = cleaned_data.get("email", "")

        user_model = get_user_model()
        user = user_model._default_manager.create_superuser(
            username=username,
            email=email,
            password=password,
        )
        user.groups.set(cleaned_data["security_groups"])

        return user, password

    @staticmethod
    def _generate_password(length: int = 24) -> str:
        return "".join(
            secrets.choice(OperatorJourneyProvisionSuperuserForm.PASSWORD_ALPHABET)
            for _ in range(length)
        )

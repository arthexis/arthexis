"""Forms for operator journey guided actions."""

from __future__ import annotations

import secrets
import string

from django import forms
from django.contrib.auth import get_user_model

from apps.groups.models import SecurityGroup


class OperatorJourneyProvisionSuperuserForm(forms.Form):
    """Create a superuser account and optionally attach a GitHub token."""

    username = forms.CharField(max_length=150, help_text="Login name for the new superuser.")
    email = forms.EmailField(required=False, help_text="Optional email address for account recovery.")
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
        widget=forms.PasswordInput(render_value=True),
        help_text="Required only when using a manual password.",
    )
    github_username = forms.CharField(
        required=False,
        max_length=255,
        help_text="Optional GitHub username for repository, release, and issue operations.",
    )
    github_token = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=True),
        help_text="Optional GitHub token to store for this new user.",
    )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("password_mode") == "custom" and not cleaned_data.get("password"):
            self.add_error("password", "Provide a password or switch to random generation.")
        return cleaned_data

    def save(self):
        """Create the superuser, assign groups, and return the account/password tuple."""

        cleaned_data = self.cleaned_data
        password = cleaned_data.get("password") or self._generate_password()
        username = cleaned_data["username"]
        email = cleaned_data.get("email", "")

        user_model = get_user_model()
        user = user_model._default_manager.create_user(
            username=username,
            email=email,
            password=password,
            is_staff=True,
            is_superuser=True,
        )
        user.groups.set(cleaned_data["security_groups"])

        github_token = (cleaned_data.get("github_token") or "").strip()
        github_username = (cleaned_data.get("github_username") or "").strip()
        if github_token:
            from apps.repos.models import GitHubToken

            GitHubToken.objects.update_or_create(
                user=user,
                defaults={"label": github_username or "Ops onboarding token", "token": github_token},
            )

        return user, password

    @staticmethod
    def _generate_password(length: int = 24) -> str:
        alphabet = string.ascii_letters + string.digits + "-._~!@#$%^&*"
        return "".join(secrets.choice(alphabet) for _ in range(length))

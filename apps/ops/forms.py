"""Forms for operator journey guided actions."""

from __future__ import annotations

import secrets
import string

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from apps.groups.models import SecurityGroup
from apps.repos.models import GitHubToken
from apps.repos.services import github as github_service
from apps.sigils.sigil_resolver import resolve_sigils


class OperatorJourneyProvisionSuperuserForm(forms.Form):
    """Create a superuser account for operations onboarding."""

    PASSWORD_ALPHABET = string.ascii_letters + string.digits + "-._~!@#$%^&*"
    UPGRADE_UPDATE_FIELDS = (
        "email",
        "is_active",
        "is_deleted",
        "is_staff",
        "is_superuser",
        "password",
        "username",
    )

    username = forms.CharField(
        max_length=150, help_text="Login name for the new superuser."
    )
    upgrade_existing_user = forms.BooleanField(
        required=False,
        help_text="Upgrade and reuse this account when the username already exists.",
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
        cleaned_data["upgrade_existing_user"] = self._is_upgrade_opt_in_requested()
        username = cleaned_data.get("username")
        existing_user = None
        if username:
            existing_user = getattr(self, "_existing_user", None)
            if existing_user is None:
                user_model = get_user_model()
                user_manager = getattr(
                    user_model, "all_objects", user_model._default_manager
                )
                existing_user = user_manager.filter(username=username).first()
                self._existing_user = existing_user
        cleaned_data["existing_user"] = existing_user

        if existing_user is not None and not cleaned_data.get("upgrade_existing_user"):
            self.add_error(
                "username",
                'Enable "Upgrade existing user" to reuse this username.',
            )
        if cleaned_data.get("upgrade_existing_user") and existing_user is None:
            self.add_error(
                "upgrade_existing_user",
                "No existing user matches this username.",
            )
        if cleaned_data.get("password_mode") == "custom" and not cleaned_data.get(
            "password"
        ):
            self.add_error(
                "password", "Provide a password or switch to random generation."
            )
        self._validate_custom_password(
            cleaned_data=cleaned_data,
            existing_user=existing_user,
        )
        return cleaned_data

    def clean_username(self):
        """Normalize the submitted username and cache any existing account."""

        user_model = get_user_model()
        username = user_model.normalize_username(
            (self.cleaned_data.get("username") or "").strip()
        )
        user_manager = getattr(user_model, "all_objects", user_model._default_manager)
        existing_user = user_manager.filter(username=username).first()
        self._existing_user = existing_user
        return username

    def clean_upgrade_existing_user(self) -> bool:
        """Treat only explicit opt-in values as enabling upgrades."""

        return self._is_upgrade_opt_in_requested()

    def _is_upgrade_opt_in_requested(self) -> bool:
        value = (self.data.get("upgrade_existing_user") or "").strip().lower()
        return value in {"1", "on", "true", "yes"}

    def _validate_custom_password(self, *, cleaned_data, existing_user) -> None:
        if cleaned_data.get("password_mode") != "custom":
            return

        password = cleaned_data.get("password")
        if not password:
            return

        user_model = get_user_model()
        if existing_user is not None:
            user = user_model(pk=existing_user.pk)
            user.username = cleaned_data.get("username", existing_user.username)
            user.email = cleaned_data.get("email", existing_user.email)
        else:
            user = user_model(
                username=cleaned_data.get("username", ""),
                email=cleaned_data.get("email", ""),
            )
        try:
            validate_password(password=password, user=user)
        except ValidationError as exc:
            self.add_error("password", exc)

    def save(self):
        """Create the superuser, assign groups, and return the account/password tuple."""

        cleaned_data = self.cleaned_data
        if cleaned_data["password_mode"] == "custom":
            password = cleaned_data["password"]
        else:
            password = self._generate_password()
        username = cleaned_data["username"]
        email = cleaned_data.get("email", "")
        existing_user = cleaned_data.get("existing_user")
        is_upgrade = existing_user is not None and cleaned_data.get(
            "upgrade_existing_user"
        )

        user_model = get_user_model()
        if is_upgrade:
            user = existing_user
            user.username = username
            user.email = email
            user.is_active = True
            if hasattr(user, "is_deleted"):
                user.is_deleted = False
            user.is_staff = True
            user.is_superuser = True
            user.set_password(password)
            user.save(update_fields=self._resolve_upgrade_update_fields(user))
        else:
            user = user_model._default_manager.create_superuser(
                username=username,
                email=email,
                password=password,
            )
        user.groups.set(cleaned_data["security_groups"])

        github_token = (cleaned_data.get("github_token") or "").strip()
        github_username = (cleaned_data.get("github_username") or "").strip()
        if github_token:
            from apps.repos.models import GitHubToken

            GitHubToken.objects.update_or_create(
                user=user,
                defaults={
                    "label": github_username or "Ops onboarding token",
                    "token": github_token,
                },
            )

        return user, password, not is_upgrade

    @staticmethod
    def _generate_password(length: int = 24) -> str:
        return "".join(
            secrets.choice(OperatorJourneyProvisionSuperuserForm.PASSWORD_ALPHABET)
            for _ in range(length)
        )

    @classmethod
    def _resolve_upgrade_update_fields(cls, user) -> list[str]:
        return [field for field in cls.UPGRADE_UPDATE_FIELDS if hasattr(user, field)]


class OperatorJourneyGitHubAccessForm(forms.Form):
    """Configure and validate the current user's GitHub token."""

    github_username = forms.CharField(
        max_length=255,
        required=False,
        help_text="Optional GitHub username to confirm against the token.",
        label="GitHub username",
    )
    token = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.PasswordInput(),
        help_text="Personal access token used for repository, release, and issue tasks.",
        label="GitHub token",
    )
    token_label = forms.CharField(
        max_length=255,
        required=False,
        help_text="Optional label shown in token admin records.",
        label="Token label",
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self._existing_token_record = (
            GitHubToken.objects.filter(user=user, group__isnull=True)
            .order_by("-pk")
            .first()
        )

    def save(self, *, token: str, username: str) -> GitHubToken:
        """Persist the token for the active user."""

        defaults = {
            "label": (username or "GitHub access token").strip(),
            "token": (token or "").strip(),
        }
        token_record, _created = GitHubToken.objects.update_or_create(
            user=self.user,
            group=None,
            defaults=defaults,
        )
        self._existing_token_record = token_record
        return token_record

    @property
    def existing_token(self) -> GitHubToken | None:
        """Return the user's existing personal GitHub token record, if present."""

        return self._existing_token_record

    def validate_connection(self) -> tuple[bool, str, str]:
        """Return whether the stored token authenticates with GitHub."""

        if self._existing_token_record is None:
            return False, "Sign in with GitHub before validating access.", ""
        stored_token = self._existing_token_record.token
        return github_service.validate_token(resolve_sigils(stored_token))

    def stored_token_raw_value(self) -> str:
        """Return the raw persisted token value without descriptor resolution."""

        if self._existing_token_record is None:
            return ""
        return str(self._existing_token_record.__dict__.get("token", "")).strip()

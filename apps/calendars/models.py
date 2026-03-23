from __future__ import annotations

from datetime import timedelta

import requests
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.sigils.fields import SigilShortAutoField
from apps.users.models import Profile

GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
REQUEST_TIMEOUT_SECONDS = 20


class GoogleAccount(Profile):
    """OAuth credentials used to access Google Calendar publishing APIs.

    Parameters:
        Inherited Django model fields plus Google OAuth credential fields.

    Returns:
        None. Django model declarations describe persisted runtime state.

    Raises:
        ValidationError: When required Google OAuth credentials are missing, the
            account is disabled, or token refresh returns an invalid payload.
    """

    owner_required = False
    profile_fields = (
        "email",
        "client_id",
        "client_secret",
        "refresh_token",
        "access_token",
    )

    email = models.EmailField(
        blank=True,
        help_text=_("Google account email used for this integration."),
    )
    client_id = SigilShortAutoField(
        max_length=255,
        help_text=_("OAuth client ID from Google Cloud."),
    )
    client_secret = SigilShortAutoField(
        max_length=255,
        help_text=_("OAuth client secret from Google Cloud."),
    )
    refresh_token = SigilShortAutoField(
        max_length=255,
        help_text=_("Refresh token granted for offline access."),
    )
    access_token = SigilShortAutoField(
        max_length=255,
        blank=True,
        help_text=_("Cached access token used for API requests."),
    )
    token_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("UTC timestamp when the cached token expires."),
    )
    scopes = models.JSONField(
        default=list,
        blank=True,
        help_text=_("OAuth scopes granted to this account."),
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text=_("Disable to block API usage from this account."),
    )

    class Meta:
        verbose_name = _("Google account")
        verbose_name_plural = _("Google accounts")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.email or self.owner_display() or f"Google account #{self.pk}"

    def _token_expired(self) -> bool:
        """Return ``True`` when the cached access token is missing or expired.

        Returns:
            bool: Whether the cached access token should be refreshed.
        """
        return (
            not self.access_token
            or not self.token_expires_at
            or timezone.now() >= self.token_expires_at
        )

    def get_access_token(self, force_refresh: bool = False) -> str:
        """Resolve an access token, refreshing with OAuth when needed.

        Parameters:
            force_refresh: Force a refresh request even when a cached token still
                appears valid.

        Returns:
            str: A bearer token suitable for Google API requests.

        Raises:
            ValidationError: If the account is disabled, credentials are
                incomplete, or the OAuth response lacks an access token.
            requests.HTTPError: Propagated when Google's token endpoint rejects
                the refresh request.
        """
        if not self.is_enabled:
            raise ValidationError(_("Google account is disabled."))

        if not force_refresh and not self._token_expired():
            return self.resolve_sigils("access_token") or self.access_token

        payload = {
            "client_id": (self.resolve_sigils("client_id") or "").strip(),
            "client_secret": (self.resolve_sigils("client_secret") or "").strip(),
            "refresh_token": (self.resolve_sigils("refresh_token") or "").strip(),
            "grant_type": "refresh_token",
        }
        if not all(payload[key] for key in ("client_id", "client_secret", "refresh_token")):
            raise ValidationError(_("Google OAuth credentials are incomplete."))

        response = requests.post(
            GOOGLE_OAUTH_TOKEN_URL,
            data=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()

        access_token = (data.get("access_token") or "").strip()
        if not access_token:
            raise ValidationError(_("Google OAuth response did not include an access token."))

        expires_in = int(data.get("expires_in") or 3600)
        self.access_token = access_token
        self.token_expires_at = timezone.now() + timedelta(seconds=max(30, expires_in - 30))
        self.save(update_fields=["access_token", "token_expires_at"])
        return access_token


class GoogleCalendar(Entity):
    """External calendar destination used for outbound event publishing."""

    account = models.ForeignKey(
        GoogleAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="calendars",
        help_text=_("Google account used to publish events to this calendar."),
    )
    name = models.CharField(
        max_length=255,
        help_text=_("Friendly display name for this outbound calendar destination."),
    )
    calendar_id = models.CharField(
        max_length=255,
        unique=True,
        help_text=_("Google Calendar ID that should receive outbound events."),
    )
    timezone = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=_("Default IANA timezone used when publishing events."),
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Optional deployment-owned metadata for outbound publishing."),
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text=_("Disable to prevent new outbound event pushes to this calendar."),
    )

    class Meta:
        verbose_name = _("Google Calendar")
        verbose_name_plural = _("Google Calendars")
        constraints = [
            models.CheckConstraint(
                condition=Q(is_enabled=False) | Q(account__isnull=False),
                name="calendar_enabled_requires_account",
            )
        ]

    def clean(self) -> None:
        """Require an account before an outbound calendar destination can be enabled."""
        super().clean()
        if self.is_enabled and self.account_id is None:
            raise ValidationError({"account": _("Enabled calendars must have a Google account.")})

    def __str__(self) -> str:  # pragma: no cover
        return self.name

from __future__ import annotations

from datetime import timedelta

import requests
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.sigils.fields import SigilShortAutoField
from apps.users.models import Profile


class GoogleAccount(Profile):
    """OAuth credentials used to access Google Drive and Sheets APIs."""

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
        """Return ``True`` when the current access token is missing or expired."""
        return (
            not self.access_token
            or not self.token_expires_at
            or timezone.now() >= self.token_expires_at
        )

    def get_access_token(self, force_refresh: bool = False) -> str:
        """Resolve an access token, refreshing with OAuth when needed."""
        if not self.is_enabled:
            raise ValidationError(_("Google account is disabled."))

        if not force_refresh and not self._token_expired():
            return self.resolve_sigils("access_token") or self.access_token

        token_url = "https://oauth2.googleapis.com/token"
        payload = {
            "client_id": (self.resolve_sigils("client_id") or "").strip(),
            "client_secret": (self.resolve_sigils("client_secret") or "").strip(),
            "refresh_token": (self.resolve_sigils("refresh_token") or "").strip(),
            "grant_type": "refresh_token",
        }
        if not all(payload[key] for key in ("client_id", "client_secret", "refresh_token")):
            raise ValidationError(_("Google OAuth credentials are incomplete."))

        response = requests.post(token_url, data=payload, timeout=20)
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


class GoogleSheet(Entity):
    """Spreadsheet metadata tracked as a virtual table source."""

    account = models.ForeignKey(
        GoogleAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sheets",
        help_text=_("Google account used to access this spreadsheet."),
    )
    name = models.CharField(
        max_length=255,
        help_text=_("Friendly name for this spreadsheet registration."),
    )
    spreadsheet_id = models.CharField(
        max_length=255,
        unique=True,
        help_text=_("Spreadsheet ID from the Google Sheets URL."),
    )
    default_worksheet = models.CharField(
        max_length=255,
        default="Sheet1",
        help_text=_("Default worksheet/tab to query when none is specified."),
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Cached metadata fetched from Google Sheets API."),
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text=_("Disable to avoid selecting this sheet in integrations."),
    )

    class Meta:
        verbose_name = _("Google Sheet")
        verbose_name_plural = _("Google Sheets")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name


class GoogleSheetColumn(Entity):
    """Introspected virtual table columns for a Google Sheet worksheet."""

    class ColumnType(models.TextChoices):
        STRING = "string", _("String")
        INTEGER = "integer", _("Integer")
        FLOAT = "float", _("Float")
        BOOLEAN = "boolean", _("Boolean")
        DATETIME = "datetime", _("Date/Time")

    sheet = models.ForeignKey(
        GoogleSheet,
        on_delete=models.CASCADE,
        related_name="columns",
    )
    worksheet = models.CharField(
        max_length=255,
        default="Sheet1",
        help_text=_("Worksheet/tab name where this column was introspected."),
    )
    name = models.CharField(
        max_length=255,
        help_text=_("Column header as seen in the worksheet."),
    )
    position = models.PositiveIntegerField(
        default=0,
        help_text=_("Zero-based column position in the worksheet."),
    )
    detected_type = models.CharField(
        max_length=20,
        choices=ColumnType.choices,
        default=ColumnType.STRING,
        help_text=_("Best-effort type inferred from sampled data."),
    )

    class Meta:
        verbose_name = _("Google Sheet column")
        verbose_name_plural = _("Google Sheet columns")
        constraints = [
            models.UniqueConstraint(
                fields=["sheet", "worksheet", "name"],
                name="gdrive_unique_sheet_column_name",
            ),
            models.UniqueConstraint(
                fields=["sheet", "worksheet", "position"],
                name="gdrive_unique_sheet_column_position",
            ),
        ]
        ordering = ["sheet", "worksheet", "position"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.sheet.name}.{self.worksheet}.{self.name}"

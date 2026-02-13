"""Models for storing Google Drive accounts and Sheets metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


SHEET_URL_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


@dataclass(slots=True)
class SheetLoadResult:
    """Result payload returned by header loading operations."""

    title: str
    worksheet_title: str
    headers: list[str]
    metadata: dict


class DriveAccount(Entity):
    """OAuth account metadata used to access Drive and Sheets APIs."""

    name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    access_token = models.TextField(blank=True, help_text=_("OAuth access token."))
    refresh_token = models.TextField(blank=True, help_text=_("OAuth refresh token."))

    class Meta:
        verbose_name = _("Drive Account")
        verbose_name_plural = _("Drive Accounts")
        ordering = ("name", "pk")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.email or self.name


class GoogleSheet(Entity):
    """Tracked Google Sheet with optional account linkage and cached headers."""

    drive_account = models.ForeignKey(
        DriveAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sheets",
    )
    title = models.CharField(max_length=200, blank=True)
    spreadsheet_id = models.CharField(max_length=200, unique=True)
    sheet_url = models.URLField(blank=True)
    worksheet_title = models.CharField(max_length=200, blank=True)
    is_public = models.BooleanField(default=False)
    headers = models.JSONField(default=list, blank=True)
    sheet_metadata = models.JSONField(default=dict, blank=True)
    last_loaded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Google Sheet")
        verbose_name_plural = _("Google Sheets")
        ordering = ("title", "spreadsheet_id", "pk")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.title or self.spreadsheet_id

    @classmethod
    def spreadsheet_id_from_url(cls, url: str) -> str:
        """Extract a spreadsheet id from a standard Google Sheets URL."""

        match = SHEET_URL_RE.search(url or "")
        return match.group(1) if match else ""

    def clean(self):
        """Normalize and validate URL/id coherence."""

        super().clean()
        if self.sheet_url and not self.spreadsheet_id:
            self.spreadsheet_id = self.spreadsheet_id_from_url(self.sheet_url)
        if self.sheet_url and not self.spreadsheet_id:
            raise ValidationError({"sheet_url": _("Enter a valid Google Sheets URL.")})

    @classmethod
    def discover_from_url(
        cls,
        *,
        sheet_url: str,
        drive_account: DriveAccount | None = None,
        is_public: bool | None = None,
    ) -> "GoogleSheet":
        """Create or update a tracked sheet from a URL and optional account."""

        spreadsheet_id = cls.spreadsheet_id_from_url(sheet_url)
        if not spreadsheet_id:
            raise ValidationError({"sheet_url": _("Unable to parse spreadsheet id from URL.")})
        obj, _ = cls.objects.update_or_create(
            spreadsheet_id=spreadsheet_id,
            defaults={
                "drive_account": drive_account,
                "sheet_url": sheet_url,
                "is_public": bool(is_public) if is_public is not None else drive_account is None,
            },
        )
        return obj

    def set_loaded_data(self, result: SheetLoadResult):
        """Persist headers and summary metadata after a sheet load."""

        self.title = result.title or self.title
        self.worksheet_title = result.worksheet_title
        self.headers = result.headers
        self.sheet_metadata = result.metadata
        self.last_loaded_at = timezone.now()
        self.save(
            update_fields=[
                "title",
                "worksheet_title",
                "headers",
                "sheet_metadata",
                "last_loaded_at",
                "updated_at",
            ]
        )

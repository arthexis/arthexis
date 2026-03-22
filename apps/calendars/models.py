from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.gdrive.models import GoogleAccount


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

    def __str__(self) -> str:  # pragma: no cover
        return self.name

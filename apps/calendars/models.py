from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.gdrive.models import GoogleAccount


def _calendar_trigger_allowed_tasks() -> set[str]:
    """Return configured allowlist for calendar trigger task dispatch."""
    return {
        str(task).strip()
        for task in getattr(settings, "CALENDAR_EVENT_TRIGGER_ALLOWED_TASKS", ())
        if str(task).strip()
    }


class GoogleCalendar(Entity):
    """Tracked Google Calendar metadata and sync state."""

    account = models.ForeignKey(
        GoogleAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="calendars",
        help_text=_("Google account used to access this calendar."),
    )
    name = models.CharField(
        max_length=255,
        help_text=_("Friendly display name for this tracked calendar."),
    )
    calendar_id = models.CharField(
        max_length=255,
        unique=True,
        help_text=_("Google Calendar ID, usually an email-like identifier."),
    )
    timezone = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=_("IANA timezone returned by Google Calendar."),
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Cached metadata payload from Google Calendar API."),
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text=_("Disable to stop synchronization and trigger execution."),
    )

    class Meta:
        verbose_name = _("Google Calendar")
        verbose_name_plural = _("Google Calendars")

    def __str__(self) -> str:  # pragma: no cover
        return self.name


class CalendarEventTrigger(Entity):
    """Rule that dispatches a Celery task when matching events are approaching."""

    calendar = models.ForeignKey(
        GoogleCalendar,
        on_delete=models.CASCADE,
        related_name="triggers",
    )
    name = models.CharField(max_length=255)
    task_name = models.CharField(
        max_length=255,
        help_text=_("Fully-qualified Celery task name to dispatch."),
    )
    lead_time_minutes = models.PositiveIntegerField(
        default=0,
        help_text=_("Run this many minutes before event start."),
    )
    summary_contains = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_("Optional case-insensitive summary fragment required to match."),
    )
    location_contains = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_("Optional case-insensitive location fragment required to match."),
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text=_("Disable to pause this trigger without deleting it."),
    )

    class Meta:
        verbose_name = _("Calendar event trigger")
        verbose_name_plural = _("Calendar event triggers")

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def clean(self) -> None:
        """Ensure calendar triggers can only target allowlisted Celery tasks."""
        super().clean()
        if self.task_name not in _calendar_trigger_allowed_tasks():
            raise ValidationError({
                "task_name": _(
                    "Task is not permitted for calendar triggers. "
                    "Update CALENDAR_EVENT_TRIGGER_ALLOWED_TASKS to allow it."
                )
            })


class CalendarEventDispatch(Entity):
    """Deduplication record of previously dispatched calendar events."""

    trigger = models.ForeignKey(
        CalendarEventTrigger,
        on_delete=models.CASCADE,
        related_name="dispatches",
    )
    event_id = models.CharField(max_length=255)
    event_updated = models.DateTimeField(
        help_text=_("Last update timestamp from Google event payload."),
    )

    class Meta:
        verbose_name = _("Calendar event dispatch")
        verbose_name_plural = _("Calendar event dispatches")
        constraints = [
            models.UniqueConstraint(
                fields=["trigger", "event_id", "event_updated"],
                name="calendars_unique_dispatch_per_event_revision",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.trigger.name}:{self.event_id}"


class CalendarEventSnapshot(Entity):
    """Latest synchronized calendar event metadata for observability."""

    calendar = models.ForeignKey(
        GoogleCalendar,
        on_delete=models.CASCADE,
        related_name="event_snapshots",
    )
    event_id = models.CharField(max_length=255)
    summary = models.CharField(max_length=500, blank=True, default="")
    location = models.CharField(max_length=500, blank=True, default="")
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    event_updated = models.DateTimeField()
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = _("Calendar event snapshot")
        verbose_name_plural = _("Calendar event snapshots")
        constraints = [
            models.UniqueConstraint(
                fields=["calendar", "event_id"],
                name="calendars_unique_snapshot_per_event",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.calendar.name}:{self.summary or self.event_id}"

    def clean(self) -> None:
        """Validate chronological consistency when both boundaries are present."""
        if self.starts_at and self.ends_at and self.ends_at < self.starts_at:
            raise ValidationError({"ends_at": _("Event end time must be after start time.")})

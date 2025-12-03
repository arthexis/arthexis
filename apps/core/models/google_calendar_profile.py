import contextlib
import logging
from datetime import date as datetime_date
from datetime import datetime as datetime_datetime
from datetime import time as datetime_time
from datetime import timezone as datetime_timezone
from typing import Any
from urllib.parse import quote, quote_plus
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from apps.sigils.fields import SigilShortAutoField

from .profile import Profile

logger = logging.getLogger(__name__)


class GoogleCalendarProfile(Profile):
    """Store Google Calendar configuration for a user or security group."""

    profile_fields = ("calendar_id", "api_key", "display_name", "timezone")

    calendar_id = SigilShortAutoField(
        max_length=255, verbose_name=_("Calendar ID")
    )
    api_key = SigilShortAutoField(max_length=255, verbose_name=_("API Key"))
    display_name = models.CharField(
        max_length=255, blank=True, verbose_name=_("Display Name")
    )
    max_events = models.PositiveIntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(20)],
        help_text=_("Number of upcoming events to display (1-20)."),
    )
    timezone = SigilShortAutoField(
        max_length=100, blank=True, verbose_name=_("Time Zone")
    )

    GOOGLE_EVENTS_URL = (
        "https://www.googleapis.com/calendar/v3/calendars/{calendar}/events"
    )
    GOOGLE_EMBED_URL = "https://calendar.google.com/calendar/embed?src={calendar}&ctz={tz}"

    class Meta:
        verbose_name = _("Google Calendar")
        verbose_name_plural = _("Google Calendars")
        constraints = [
            models.CheckConstraint(
                condition=(
                    (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="googlecalendarprofile_requires_owner",
            )
        ]

    def __str__(self):  # pragma: no cover - simple representation
        label = self.get_display_name()
        return label or self.resolved_calendar_id()

    def resolved_calendar_id(self) -> str:
        value = self.resolve_sigils("calendar_id")
        return value or self.calendar_id or ""

    def resolved_api_key(self) -> str:
        value = self.resolve_sigils("api_key")
        return value or self.api_key or ""

    def resolved_timezone(self) -> str:
        value = self.resolve_sigils("timezone")
        return value or self.timezone or ""

    def get_timezone(self) -> ZoneInfo:
        tz_name = self.resolved_timezone() or settings.TIME_ZONE
        try:
            return ZoneInfo(tz_name)
        except Exception:
            return ZoneInfo("UTC")

    def get_display_name(self) -> str:
        value = self.resolve_sigils("display_name")
        if value:
            return value
        if self.display_name:
            return self.display_name
        return ""

    def build_events_url(self) -> str:
        calendar = self.resolved_calendar_id().strip()
        if not calendar:
            return ""
        encoded = quote(calendar, safe="@")
        return self.GOOGLE_EVENTS_URL.format(calendar=encoded)

    def build_calendar_url(self) -> str:
        calendar = self.resolved_calendar_id().strip()
        if not calendar:
            return ""
        tz = self.get_timezone().key
        encoded_calendar = quote_plus(calendar)
        encoded_tz = quote_plus(tz)
        return self.GOOGLE_EMBED_URL.format(calendar=encoded_calendar, tz=encoded_tz)

    def _parse_event_point(self, data: dict) -> tuple[datetime_datetime | None, bool]:
        if not isinstance(data, dict):
            return None, False

        tz_name = data.get("timeZone")
        default_tz = self.get_timezone()
        tzinfo = default_tz
        if tz_name:
            try:
                tzinfo = ZoneInfo(tz_name)
            except Exception:
                tzinfo = default_tz

        timestamp = data.get("dateTime")
        if timestamp:
            dt = parse_datetime(timestamp)
            if dt is None:
                try:
                    dt = datetime_datetime.fromisoformat(
                        timestamp.replace("Z", "+00:00")
                    )
                except ValueError:
                    dt = None
            if dt is not None and dt.tzinfo is None:
                dt = dt.replace(tzinfo=tzinfo)
            return dt, False

        date_value = data.get("date")
        if date_value:
            try:
                day = datetime_date.fromisoformat(date_value)
            except ValueError:
                return None, True
            dt = datetime_datetime.combine(day, datetime_time.min, tzinfo=tzinfo)
            return dt, True

        return None, False

    def fetch_events(self, *, max_results: int | None = None) -> list[dict[str, Any]]:
        calendar_id = self.resolved_calendar_id().strip()
        api_key = self.resolved_api_key().strip()
        if not calendar_id or not api_key:
            return []

        url = self.build_events_url()
        if not url:
            return []

        now = timezone.now().astimezone(datetime_timezone.utc).replace(microsecond=0)
        params = {
            "key": api_key,
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeMin": now.isoformat().replace("+00:00", "Z"),
            "maxResults": max_results or self.max_events or 5,
        }

        response = None
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            logger.warning(
                "Failed to fetch Google Calendar events for profile %s", self.pk,
                exc_info=True,
            )
            return []
        finally:
            if response is not None:
                with contextlib.suppress(Exception):
                    response.close()

        items = payload.get("items")
        if not isinstance(items, list):
            return []

        events: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            start, all_day = self._parse_event_point(item.get("start") or {})
            end, _ = self._parse_event_point(item.get("end") or {})
            summary = item.get("summary") or ""
            link = item.get("htmlLink") or ""
            location = item.get("location") or ""
            if start is None:
                continue
            events.append(
                {
                    "summary": summary,
                    "start": start,
                    "end": end,
                    "all_day": all_day,
                    "html_link": link,
                    "location": location,
                }
            )

        events.sort(key=lambda event: event.get("start") or timezone.now())
        return events

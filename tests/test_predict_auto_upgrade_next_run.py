from datetime import datetime, timedelta

import pytest

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.system import upgrade
from apps.core.system.ui import _format_timestamp

pytestmark = pytest.mark.integration

class DummySchedule:
    def __init__(
        self,
        now_value: datetime,
        remaining: timedelta | Exception,
        *,
        make_aware_error: bool = False,
        now_error: bool = False,
    ):
        self._now_value = now_value
        self._remaining = remaining
        self._make_aware_error = make_aware_error
        self._now_error = now_error

    def now(self):
        if self._now_error:
            raise ValueError("now error")
        return self._now_value

    def maybe_make_aware(self, value):
        if self._make_aware_error:
            raise ValueError("awareness failure")
        if value is None:
            return None
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value

    def remaining_estimate(self, reference):
        if isinstance(self._remaining, Exception):
            raise self._remaining
        return self._remaining

class DummyTask:
    def __init__(
        self,
        *,
        enabled: bool = True,
        schedule=None,
        start_time: datetime | None = None,
        last_run_at: datetime | None = None,
    ):
        self.enabled = enabled
        self.schedule = schedule
        self.start_time = start_time
        self.last_run_at = last_run_at

def test_disabled_task_returns_disabled_label():
    task = DummyTask(enabled=False, schedule=object())

    assert upgrade._predict_auto_upgrade_next_run(task) == str(_("Disabled"))

def test_start_time_in_future_uses_normalized_time(settings):
    now = timezone.now()
    raw_start = now.replace(tzinfo=None) + timedelta(minutes=30)
    schedule = DummySchedule(now, timedelta(minutes=10))
    task = DummyTask(schedule=schedule, start_time=raw_start)

    expected = _format_timestamp(
        timezone.make_aware(raw_start, timezone.get_current_timezone())
    )

    assert upgrade._predict_auto_upgrade_next_run(task) == expected

def test_last_run_reference_with_aware_datetime(settings):
    now = timezone.now()
    last_run = now - timedelta(minutes=5)
    schedule = DummySchedule(now, timedelta(minutes=15))
    task = DummyTask(schedule=schedule, last_run_at=last_run)

    expected = _format_timestamp(now + timedelta(minutes=15))

    assert upgrade._predict_auto_upgrade_next_run(task) == expected

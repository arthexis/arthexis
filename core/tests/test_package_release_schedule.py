from __future__ import annotations

import os
import tempfile
from datetime import datetime as datetime_datetime, time as datetime_time, timedelta
from pathlib import Path
from unittest import mock

import django
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

try:  # Use pytest helper when available
    from tests.conftest import safe_setup as _safe_setup  # type: ignore
except Exception:  # pragma: no cover - fallback for direct execution
    _safe_setup = None

if _safe_setup is not None:
    _safe_setup()
else:  # pragma: no cover - fallback when pytest fixtures are unavailable
    django.setup()
from core import release_workflow, views as core_views
from core.models import CountdownTimer, Package, PackageRelease, ReleaseManager
from core.tasks import execute_scheduled_release


def _clocked_schedule_model():
    from django_celery_beat.models import ClockedSchedule

    return ClockedSchedule


def _periodic_task_model():
    from django_celery_beat.models import PeriodicTask

    return PeriodicTask


def _scheduled_release_tasks(release_id: int):
    lookup = f'"release_id": {release_id}'
    return _periodic_task_model().objects.filter(
        task="core.tasks.run_scheduled_release", kwargs__contains=lookup
    )


class PackageReleaseScheduleTests(TestCase):
    def setUp(self) -> None:
        self.package = Package.objects.create(name="pkg-schedule", is_active=True)
        self.release = PackageRelease.objects.create(
            package=self.package,
            version="9.9.9",
            revision="",
        )

    def _set_schedule(self, when: timezone.datetime) -> None:
        self.release.scheduled_date = when.date()
        self.release.scheduled_time = datetime_time(
            when.hour, when.minute, when.second
        )
        self.release.save()
        self.release.refresh_from_db()

    def test_clean_requires_both_fields(self):
        self.release.scheduled_date = timezone.localdate() + timedelta(days=1)
        with self.assertRaises(ValidationError):
            self.release.full_clean()

    def test_schedule_creation_builds_periodic_task(self):
        when = timezone.now() + timedelta(days=1)
        self._set_schedule(when)
        self.assertIsNotNone(self.release.scheduled_task)
        task = self.release.scheduled_task
        self.assertEqual(task.task, "core.tasks.run_scheduled_release")
        payload = task.kwargs
        self.assertIn(str(self.release.pk), payload)
        scheduled = task.clocked
        self.assertIsNotNone(scheduled)
        expected = self.release.scheduled_datetime
        self.assertEqual(scheduled.clocked_time, expected)
        self.assertTrue(task.one_off)
        self.assertTrue(task.enabled)

    def test_schedule_creation_builds_countdown_timer(self):
        when = timezone.now() + timedelta(days=1)
        self._set_schedule(when)

        timer = self.release.countdown_timer
        self.assertEqual(timer.package_release, self.release)
        self.assertEqual(timer.scheduled_for, self.release.scheduled_datetime)
        self.assertEqual(timer.title, f"{self.package.name} {self.release.version} release")
        self.assertFalse(timer.is_published)

    def test_schedule_updates_remove_orphaned_clock(self):
        when = timezone.now() + timedelta(days=1)
        self._set_schedule(when)
        old_clock_id = self.release.scheduled_task.clocked_id
        updated = when + timedelta(hours=2)
        self._set_schedule(updated)
        self.assertEqual(
            self.release.scheduled_task.clocked.clocked_time,
            self.release.scheduled_datetime,
        )
        self.assertFalse(
            _clocked_schedule_model().objects.filter(pk=old_clock_id).exists()
        )

        self.release.countdown_timer.refresh_from_db()
        self.assertEqual(
            self.release.countdown_timer.scheduled_for,
            self.release.scheduled_datetime,
        )

    def test_clear_schedule_removes_periodic_task(self):
        when = timezone.now() + timedelta(days=1)
        self._set_schedule(when)
        self.release.clear_schedule()
        self.release.refresh_from_db()
        self.assertIsNone(self.release.scheduled_date)
        self.assertIsNone(self.release.scheduled_time)
        self.assertIsNone(self.release.scheduled_task)
        self.assertFalse(_scheduled_release_tasks(self.release.pk).exists())

        with self.assertRaises(CountdownTimer.DoesNotExist):
            _ = self.release.countdown_timer


class ScheduledReleaseTaskTests(TestCase):
    def setUp(self) -> None:
        package = Package.objects.create(name="pkg-auto", is_active=True)
        self.release = PackageRelease.objects.create(
            package=package,
            version="8.8.8",
            revision="",
        )
        when = timezone.now() + timedelta(days=1)
        self.release.scheduled_date = when.date()
        self.release.scheduled_time = datetime_time(
            when.hour, when.minute, when.second
        )
        self.release.save()

    @mock.patch("core.tasks.release_workflow.run_headless_publish")
    def test_execute_scheduled_release_runs_workflow(self, workflow_mock):
        workflow_mock.return_value = Path("logs/test.log")
        execute_scheduled_release(self.release.pk)
        workflow_mock.assert_called_once()
        args, kwargs = workflow_mock.call_args
        self.assertEqual(args[0].pk, self.release.pk)
        self.assertTrue(kwargs.get("auto_release"))
        self.release.refresh_from_db()
        self.assertIsNone(self.release.scheduled_date)
        self.assertFalse(_scheduled_release_tasks(self.release.pk).exists())

    @mock.patch("core.tasks.release_workflow.run_headless_publish")
    def test_execute_scheduled_release_propagates_errors(self, workflow_mock):
        workflow_mock.side_effect = release_workflow.ReleaseWorkflowError("boom")
        with self.assertRaises(release_workflow.ReleaseWorkflowError):
            execute_scheduled_release(self.release.pk)
        self.release.refresh_from_db()
        self.assertIsNone(self.release.scheduled_date)
        self.assertFalse(_scheduled_release_tasks(self.release.pk).exists())


class ReleaseManagerApprovalAutoTests(TestCase):
    def setUp(self) -> None:
        package = Package.objects.create(name="pkg-approval", is_active=True)
        user_model = get_user_model()
        user = user_model.objects.create_user(username="approver", password="pwd")
        manager = ReleaseManager.objects.create(user=user, pypi_token="token-value")
        self.release = PackageRelease.objects.create(
            package=package,
            release_manager=manager,
            version="7.7.7",
            revision="",
        )

    def test_auto_release_bypasses_manual_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "log.txt"
            ctx: dict[str, object] = {"auto_release": True}
            core_views._step_release_manager_approval(self.release, ctx, log_path)
            self.assertNotIn("awaiting_approval", ctx)
            self.assertNotIn("release_approval", ctx)
            self.assertTrue(log_path.exists())
            contents = log_path.read_text(encoding="utf-8")
            self.assertIn("Scheduled release automatically approved", contents)


class CountdownTimerReleaseSyncTests(TestCase):
    def setUp(self) -> None:
        package = Package.objects.create(name="pkg-timer-sync", is_active=True)
        self.release = PackageRelease.objects.create(
            package=package,
            version="6.6.6",
            revision="",
        )

    def test_timer_saves_schedule_on_release(self):
        scheduled_for = timezone.now() + timedelta(days=2)
        CountdownTimer.objects.create(
            title="Release Countdown",
            scheduled_for=scheduled_for,
            package_release=self.release,
        )

        self.release.refresh_from_db()
        expected = timezone.localtime(
            scheduled_for, timezone.get_current_timezone()
        )
        self.assertEqual(self.release.scheduled_datetime, expected)

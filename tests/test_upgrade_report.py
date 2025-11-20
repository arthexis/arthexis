from __future__ import annotations

from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
from unittest import mock

from django.conf import settings
from django.contrib import messages
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import DatabaseError
from django.test import SimpleTestCase, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext

from core import system


class UpgradeReportTests(SimpleTestCase):
    databases = {"default"}

    def setUp(self):
        self.factory = RequestFactory()

    def _prepare_request(self, request):
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)
        request.session.save()
        messages_storage = FallbackStorage(request)
        setattr(request, "_messages", messages_storage)

    def test_build_auto_upgrade_report_reads_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            locks_dir = base / "locks"
            logs_dir = base / "logs"
            locks_dir.mkdir()
            logs_dir.mkdir()

            (locks_dir / "auto_upgrade.lck").write_text("latest", encoding="utf-8")
            (locks_dir / "auto_upgrade_skip_revisions.lck").write_text(
                "abc123\n\nxyz789\n",
                encoding="utf-8",
            )
            (logs_dir / "auto-upgrade.log").write_text(
                "2024-01-01T00:00:00+00:00 first run\n"
                "2024-01-01T01:00:00+00:00 second run\n",
                encoding="utf-8",
            )

            schedule_stub = {
                "available": False,
                "configured": False,
                "enabled": False,
                "one_off": False,
                "queue": "",
                "schedule": "",
                "start_time": "",
                "last_run_at": "",
                "next_run": "",
                "total_run_count": 0,
                "description": "",
                "expires": "",
                "task": "",
                "name": system.AUTO_UPGRADE_TASK_NAME,
                "error": "",
                "task_admin_url": "",
                "config_admin_url": "",
                "config_type": "",
            }

            with override_settings(BASE_DIR=str(base)):
                with mock.patch(
                    "core.system._load_auto_upgrade_schedule",
                    return_value=schedule_stub,
                ), mock.patch(
                    "core.system._suite_uptime",
                    return_value="5 hours",
                ):
                    report = system._build_auto_upgrade_report(limit=10)

        self.assertTrue(report["settings"]["enabled"])
        self.assertTrue(report["settings"]["is_latest"])
        self.assertEqual(report["settings"]["mode"], "latest")
        self.assertEqual(
            report["settings"]["skip_revisions"],
            ["abc123", "xyz789"],
        )
        self.assertEqual(
            [entry["message"] for entry in report["log_entries"]],
            ["second run", "first run"],
        )
        self.assertFalse(report["log_error"])
        self.assertTrue(report["settings"]["log_path"].endswith("auto-upgrade.log"))
        self.assertEqual(report["settings"]["suite_uptime"], "5 hours")
        self.assertIn("local_revision", report["settings"])
        self.assertIn("origin_revision", report["settings"])
        self.assertIn("origin_revision_error", report["settings"])

    def test_build_auto_upgrade_report_includes_revisions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            locks_dir = base / "locks"
            logs_dir = base / "logs"
            locks_dir.mkdir()
            logs_dir.mkdir()

            (locks_dir / "auto_upgrade.lck").write_text("latest", encoding="utf-8")

            schedule_stub = {
                "available": False,
                "configured": False,
                "enabled": False,
                "one_off": False,
                "queue": "",
                "schedule": "",
                "start_time": "",
                "last_run_at": "",
                "next_run": "",
                "total_run_count": 0,
                "description": "",
                "expires": "",
                "task": "",
                "name": system.AUTO_UPGRADE_TASK_NAME,
                "error": "",
                "task_admin_url": "",
                "config_admin_url": "",
                "config_type": "",
            }

            remote_proc = mock.Mock(returncode=0, stdout="git@example.com/repo.git", stderr="")
            fetch_proc = mock.Mock(returncode=0, stdout="", stderr="")

            with override_settings(BASE_DIR=str(base)):
                with mock.patch(
                    "core.system._load_auto_upgrade_schedule",
                    return_value=schedule_stub,
                ), mock.patch(
                    "core.system._suite_uptime",
                    return_value="5 hours",
                ), mock.patch(
                    "core.system.subprocess.run",
                    side_effect=[remote_proc, fetch_proc],
                ) as mock_run, mock.patch(
                    "core.system.subprocess.check_output"
                ) as mock_check_output:
                    mock_check_output.side_effect = ["localrev\n", "originrev\n"]
                    revision_info = system._load_upgrade_revision_info(base)
                    report = system._build_auto_upgrade_report(
                        limit=1, revision_info=revision_info
                    )

        self.assertEqual(report["settings"]["local_revision"], "localrev")
        self.assertEqual(report["settings"]["origin_revision"], "originrev")
        self.assertFalse(report["settings"]["origin_revision_error"])
        mock_run.assert_has_calls(
            [
                mock.call(
                    ["git", "remote", "get-url", "origin"],
                    cwd=base,
                    capture_output=True,
                    text=True,
                    check=False,
                ),
                mock.call(
                    ["git", "fetch", "origin", "main"],
                    cwd=base,
                    check=True,
                    capture_output=True,
                    text=True,
                ),
            ]
        )
        mock_check_output.assert_has_calls(
            [
                mock.call(
                    ["git", "rev-parse", "HEAD"],
                    cwd=base,
                    stderr=subprocess.STDOUT,
                    text=True,
                ),
                mock.call(
                    ["git", "rev-parse", "origin/main"],
                    cwd=base,
                    stderr=subprocess.STDOUT,
                    text=True,
                ),
            ]
        )

    def test_build_auto_upgrade_report_skips_revision_check_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            locks_dir = base / "locks"
            logs_dir = base / "logs"
            locks_dir.mkdir()
            logs_dir.mkdir()

            (locks_dir / "auto_upgrade.lck").write_text("latest", encoding="utf-8")

            schedule_stub = {
                "available": False,
                "configured": False,
                "enabled": False,
                "one_off": False,
                "queue": "",
                "schedule": "",
                "start_time": "",
                "last_run_at": "",
                "next_run": "",
                "total_run_count": 0,
                "description": "",
                "expires": "",
                "task": "",
                "name": system.AUTO_UPGRADE_TASK_NAME,
                "error": "",
                "task_admin_url": "",
                "config_admin_url": "",
                "config_type": "",
            }

            with override_settings(BASE_DIR=str(base)):
                with mock.patch(
                    "core.system._load_auto_upgrade_schedule",
                    return_value=schedule_stub,
                ), mock.patch(
                    "core.system._suite_uptime",
                    return_value="5 hours",
                ), mock.patch(
                    "core.system._load_upgrade_revision_info"
                ) as revision_loader:
                    report = system._build_auto_upgrade_report(limit=5)

        revision_loader.assert_not_called()
        self.assertEqual(report["settings"]["local_revision"], "")
        self.assertEqual(report["settings"]["origin_revision"], "")

    def test_revision_check_view_stores_session_data(self):
        request = self.factory.post(reverse("admin:system-upgrade-check-revision"))
        self._prepare_request(request)

        revision_payload = {
            "local_revision": "abc",
            "origin_revision": "def",
            "origin_revision_error": "",
        }

        with override_settings(BASE_DIR="/tmp"):
            with mock.patch(
                "core.system._load_upgrade_revision_info",
                return_value=revision_payload,
            ) as loader:
                response = system._system_upgrade_revision_check_view(request)

        loader.assert_called_once()
        stored = request.session.get(system.UPGRADE_REVISION_SESSION_KEY)
        self.assertEqual(stored["local_revision"], "abc")
        self.assertIn("revision_checked_at", stored)
        self.assertEqual(response.status_code, 302)

    def test_suite_uptime_uses_datetime_timezone(self):
        fake_now = datetime(2024, 1, 1, 1, tzinfo=datetime_timezone.utc)
        boot_timestamp = fake_now.timestamp() - 3600

        fake_psutil = mock.Mock()
        fake_psutil.boot_time.return_value = boot_timestamp

        with mock.patch.dict(sys.modules, {"psutil": fake_psutil}):
            with mock.patch("core.system.timezone.now", return_value=fake_now):
                uptime = system._suite_uptime()

        self.assertEqual(uptime, "1\xa0hour")
        fake_psutil.boot_time.assert_called_once_with()

    def test_suite_uptime_prefers_lockfile(self):
        fake_now = datetime(2024, 1, 1, 3, tzinfo=datetime_timezone.utc)
        lock_payload = {"started_at": "2024-01-01T01:00:00+00:00"}

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            locks_dir = base / "locks"
            locks_dir.mkdir()
            lock_path = locks_dir / system.SUITE_UPTIME_LOCK_NAME
            lock_path.write_text(json.dumps(lock_payload), encoding="utf-8")
            os.utime(lock_path, (fake_now.timestamp(), fake_now.timestamp()))

            with override_settings(BASE_DIR=str(base)):
                with mock.patch("core.system.timezone.now", return_value=fake_now):
                    details = system._suite_uptime_details()

        self.assertEqual(details["uptime"], "2\xa0hours")
        self.assertEqual(
            details["boot_time"],
            datetime(2024, 1, 1, 1, tzinfo=datetime_timezone.utc),
        )
        self.assertTrue(details["available"])

    def test_suite_uptime_lockfile_must_be_recent(self):
        fake_now = datetime(2024, 1, 1, 3, tzinfo=datetime_timezone.utc)
        lock_payload = {"started_at": "2024-01-01T01:00:00+00:00"}
        boot_timestamp = fake_now.timestamp() - 1800

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            locks_dir = base / "locks"
            locks_dir.mkdir()
            lock_path = locks_dir / system.SUITE_UPTIME_LOCK_NAME
            lock_path.write_text(json.dumps(lock_payload), encoding="utf-8")
            stale_heartbeat = fake_now - (
                system.SUITE_UPTIME_LOCK_MAX_AGE + timedelta(minutes=5)
            )
            os.utime(lock_path, (stale_heartbeat.timestamp(), stale_heartbeat.timestamp()))

            fake_psutil = mock.Mock()
            fake_psutil.boot_time.return_value = boot_timestamp

            with override_settings(BASE_DIR=str(base)):
                with mock.patch.dict(sys.modules, {"psutil": fake_psutil}):
                    with mock.patch(
                        "core.system.timezone.now", return_value=fake_now
                    ):
                        details = system._suite_uptime_details()

        expected_boot = datetime.fromtimestamp(boot_timestamp, tz=datetime_timezone.utc)
        self.assertEqual(details["boot_time"], expected_boot)
        fake_psutil.boot_time.assert_called_once_with()

    def test_load_auto_upgrade_log_entries_limits_and_orders_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            logs_dir = base / "logs"
            logs_dir.mkdir()

            log_path = logs_dir / "auto-upgrade.log"
            lines = [
                f"2024-01-01T00:{index:02d}:00+00:00 entry {index}"
                for index in range(50)
            ]
            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            info = system._load_auto_upgrade_log_entries(base)

        entries = info["entries"]
        self.assertEqual(len(entries), system.AUTO_UPGRADE_LOG_LIMIT)
        self.assertEqual(entries[0]["message"], "entry 49")
        self.assertEqual(entries[-1]["message"], "entry 20")

    def test_load_auto_upgrade_schedule_uses_task_metadata(self):
        class DummySchedule:
            def __str__(self) -> str:
                return "every 5 minutes"

        class DummyTask:
            def __init__(self):
                self.enabled = True
                self.one_off = False
                self.queue = "default"
                self.total_run_count = 7
                self.description = "Auto-upgrade"
                self.task = "core.tasks.check_github_updates"
                self.name = system.AUTO_UPGRADE_TASK_NAME
                self.start_time = timezone.now()
                self.last_run_at = timezone.now()
                self.expires = None
                self._schedule = DummySchedule()
                self.pk = 42
                self.interval_id = 24
                self.crontab_id = None
                self.solar_id = None
                self.clocked_id = None

            @property
            def schedule(self):
                return self._schedule

        dummy_task = DummyTask()
        expected_start = system._format_timestamp(dummy_task.start_time)
        expected_last_run = system._format_timestamp(dummy_task.last_run_at)

        with mock.patch(
            "core.system._get_auto_upgrade_periodic_task",
            return_value=(dummy_task, True, ""),
        ), mock.patch(
            "core.system._auto_upgrade_next_check",
            return_value="Soon",
        ), mock.patch(
            "core.system._reverse_admin_url",
            side_effect=["/admin/task/42/", "/admin/interval/24/"],
        ) as mock_reverse:
            info = system._load_auto_upgrade_schedule()

        self.assertTrue(info["available"])
        self.assertTrue(info["configured"])
        self.assertTrue(info["enabled"])
        self.assertEqual(info["schedule"], "every 5 minutes")
        self.assertEqual(info["next_run"], "Soon")
        self.assertEqual(info["total_run_count"], 7)
        self.assertEqual(info["task"], dummy_task.task)
        self.assertEqual(info["name"], dummy_task.name)
        self.assertEqual(info["start_time"], expected_start)
        self.assertEqual(info["last_run_at"], expected_last_run)
        self.assertEqual(info["task_admin_url"], "/admin/task/42/")
        self.assertEqual(info["config_admin_url"], "/admin/interval/24/")
        self.assertEqual(info["config_type"], "interval")
        mock_reverse.assert_has_calls(
            [
                mock.call("admin:django_celery_beat_periodictask_change", 42),
                mock.call("admin:django_celery_beat_intervalschedule_change", 24),
            ]
        )

    def test_get_auto_upgrade_periodic_task_recovers_after_error(self):
        dummy_task = object()

        class DummyDoesNotExist(Exception):
            pass

        query_mock = mock.Mock()
        query_mock.only.return_value = query_mock
        query_mock.get.side_effect = [DatabaseError("boom"), dummy_task]

        objects_mock = mock.Mock()
        objects_mock.select_related.return_value = query_mock

        with mock.patch(
            "django_celery_beat.models.PeriodicTask"
        ) as periodic_mock, mock.patch(
            "core.system.ensure_auto_upgrade_periodic_task"
        ) as ensure_mock:
            periodic_mock.DoesNotExist = DummyDoesNotExist
            periodic_mock.objects = objects_mock

            task, available, error = system._get_auto_upgrade_periodic_task()

        self.assertIs(task, dummy_task)
        self.assertTrue(available)
        self.assertEqual(error, "")
        ensure_mock.assert_called_once_with()
        self.assertEqual(query_mock.get.call_count, 2)

    def test_get_auto_upgrade_periodic_task_handles_missing_task_after_retry(self):
        class DummyDoesNotExist(Exception):
            pass

        query_mock = mock.Mock()
        query_mock.only.return_value = query_mock
        query_mock.get.side_effect = [DummyDoesNotExist(), DummyDoesNotExist()]

        objects_mock = mock.Mock()
        objects_mock.select_related.return_value = query_mock

        with mock.patch(
            "django_celery_beat.models.PeriodicTask"
        ) as periodic_mock, mock.patch(
            "core.system.ensure_auto_upgrade_periodic_task"
        ) as ensure_mock:
            periodic_mock.DoesNotExist = DummyDoesNotExist
            periodic_mock.objects = objects_mock

            task, available, error = system._get_auto_upgrade_periodic_task()

        self.assertIsNone(task)
        self.assertTrue(available)
        self.assertEqual(error, "")
        ensure_mock.assert_called_once_with()
        self.assertEqual(query_mock.get.call_count, 2)

    def test_trigger_upgrade_check_uses_async_queue(self):
        with mock.patch("core.system.check_github_updates") as mock_task:
            mock_task.delay = mock.Mock()

            queued = system._trigger_upgrade_check()

        self.assertTrue(queued)
        mock_task.delay.assert_called_once_with()
        mock_task.assert_not_called()

    def test_trigger_upgrade_check_uses_async_queue_with_override(self):
        with mock.patch("core.system.check_github_updates") as mock_task:
            mock_task.delay = mock.Mock()

            queued = system._trigger_upgrade_check(channel_override="latest")

        self.assertTrue(queued)
        mock_task.delay.assert_called_once_with(channel_override="latest")
        mock_task.assert_not_called()

    def test_trigger_upgrade_check_falls_back_to_sync(self):
        with mock.patch("core.system.check_github_updates") as mock_task:
            mock_task.delay = mock.Mock(side_effect=RuntimeError("broker down"))

            queued = system._trigger_upgrade_check()

        self.assertFalse(queued)
        mock_task.delay.assert_called_once_with()
        mock_task.assert_called_once_with()

    def test_trigger_upgrade_check_view_adds_success_message(self):
        request = self.factory.post(reverse("admin:system-upgrade-run-check"))
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        request.user = mock.Mock(is_staff=True, is_active=True)

        with mock.patch(
            "core.system._trigger_upgrade_check", return_value=True
        ) as mock_trigger:
            response = system._system_trigger_upgrade_check_view(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin:system-upgrade-report"))
        stored = list(messages.get_messages(request))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].level, messages.SUCCESS)
        self.assertEqual(
            stored[0].message,
            gettext("Upgrade check requested. The task will run shortly."),
        )
        mock_trigger.assert_called_once_with(channel_override=None)

    def test_trigger_upgrade_check_view_clears_skip_revisions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            locks_dir = base / "locks"
            locks_dir.mkdir()
            skip_file = locks_dir / "auto_upgrade_skip_revisions.lck"
            skip_file.write_text("abc123\n", encoding="utf-8")

            request = self.factory.post(reverse("admin:system-upgrade-run-check"))
            SessionMiddleware(lambda req: None).process_request(request)
            request.session.save()
            setattr(request, "_messages", FallbackStorage(request))
            request.user = mock.Mock(is_staff=True, is_active=True)

            with override_settings(BASE_DIR=str(base)):
                with mock.patch(
                    "core.system._trigger_upgrade_check", return_value=True
                ) as mock_trigger:
                    response = system._system_trigger_upgrade_check_view(request)

            self.assertEqual(response.status_code, 302)
            self.assertFalse(skip_file.exists())
            mock_trigger.assert_called_once_with(channel_override=None)

    def test_trigger_upgrade_check_view_allows_channel_override(self):
        request = self.factory.post(
            reverse("admin:system-upgrade-run-check"),
            data={"channel": "latest"},
        )
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        request.user = mock.Mock(is_staff=True, is_active=True)

        with mock.patch(
            "core.system._trigger_upgrade_check", return_value=True
        ) as mock_trigger:
            response = system._system_trigger_upgrade_check_view(request)

        self.assertEqual(response.status_code, 302)
        stored = list(messages.get_messages(request))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].level, messages.SUCCESS)
        expected_message = "{} {}".format(
            gettext("Upgrade check requested. The task will run shortly."),
            gettext(
                "It will run using the %(channel)s channel for this execution without changing the configured mode."
            )
            % {"channel": gettext("Latest")},
        )
        self.assertEqual(stored[0].message, expected_message)
        mock_trigger.assert_called_once_with(channel_override="latest")

    def test_trigger_upgrade_check_view_reports_error(self):
        request = self.factory.post(reverse("admin:system-upgrade-run-check"))
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        request.user = mock.Mock(is_staff=True, is_active=True)

        with mock.patch(
            "core.system._trigger_upgrade_check",
            side_effect=RuntimeError("oops"),
        ):
            response = system._system_trigger_upgrade_check_view(request)

        self.assertEqual(response.status_code, 302)
        stored = list(messages.get_messages(request))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].level, messages.ERROR)
        self.assertEqual(
            stored[0].message,
            gettext("Unable to trigger an upgrade check: %(error)s") % {"error": "oops"},
        )

    def test_system_upgrade_report_view_includes_failover_status(self):
        request = self.factory.get(reverse("admin:system-upgrade-report"))
        request.user = mock.Mock(is_staff=True, is_active=True)

        report_stub = {"settings": {}, "schedule": {}, "log_entries": [], "log_error": ""}

        with mock.patch(
            "core.system._build_auto_upgrade_report", return_value=report_stub
        ) as mock_report, mock.patch(
            "core.system.read_failover_status", return_value={"detail": "failover"}
        ) as mock_failover:
            response = system._system_upgrade_report_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertIs(response.context_data["auto_upgrade_report"], report_stub)
        self.assertEqual(response.context_data["failover_status"], {"detail": "failover"})
        mock_report.assert_called_once_with()
        mock_failover.assert_called_once_with(Path(settings.BASE_DIR))

    def test_trigger_upgrade_check_view_respects_next_parameter(self):
        request = self.factory.post(
            reverse("admin:system-upgrade-run-check"),
            data={"next": "/admin/"},
        )
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        request.user = mock.Mock(is_staff=True, is_active=True)

        with mock.patch(
            "core.system._trigger_upgrade_check", return_value=True
        ) as mock_trigger:
            response = system._system_trigger_upgrade_check_view(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/admin/")
        mock_trigger.assert_called_once_with(channel_override=None)

    def test_clear_failover_lock_view_clears_and_redirects(self):
        request = self.factory.post(
            reverse("admin:system-upgrade-dismiss-failover"),
            data={"next": "/admin/"},
        )
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        request.user = mock.Mock(is_staff=True, is_active=True)

        with mock.patch("core.system.clear_failover_lock") as mock_clear:
            response = system._system_clear_failover_lock_view(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/admin/")
        mock_clear.assert_called_once()
        stored = list(messages.get_messages(request))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].level, messages.SUCCESS)
        self.assertEqual(
            stored[0].message,
            gettext("Failover alert dismissed. Auto-upgrade retries remain available."),
        )

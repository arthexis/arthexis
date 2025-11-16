import os
import sys
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone


class AdminSystemUptimeReportTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )

    @mock.patch("core.system._load_shutdown_periods")
    @mock.patch("core.system.timezone.now")
    def test_renders_uptime_segments(self, mock_now, mock_load_shutdowns):
        mock_now.return_value = datetime(2024, 7, 16, 12, 0, tzinfo=datetime_timezone.utc)
        mock_load_shutdowns.return_value = (
            [
                (
                    datetime(2024, 7, 16, 6, 0, tzinfo=datetime_timezone.utc),
                    datetime(2024, 7, 16, 7, 0, tzinfo=datetime_timezone.utc),
                )
            ],
            None,
        )

        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:system-uptime-report"))

        context = getattr(response, "context_data", None)
        if context is None:
            context = response.context
        self.assertIsNotNone(context)
        report = context["uptime_report"]
        self.assertContains(response, "Uptime Report")
        self.assertContains(response, "Last 24 hours")
        window = report["windows"][0]
        self.assertEqual(window["uptime_percent"], 95.8)
        self.assertEqual(window["downtime_percent"], 4.2)
        self.assertEqual(len(window["downtime_events"]), 1)
        expected_start = timezone.localtime(
            datetime(2024, 7, 16, 6, 0, tzinfo=datetime_timezone.utc)
        ).strftime("%Y-%m-%d %H:%M")
        self.assertEqual(window["downtime_events"][0]["start"], expected_start)

    @mock.patch("core.system.subprocess.run", side_effect=FileNotFoundError)
    def test_handles_missing_last_command(self, mock_run):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:system-uptime-report"))

        self.assertContains(
            response,
            "The `last` command is not available on this node.",
        )

    @mock.patch(
        "core.system._suite_uptime_details",
        return_value={
            "uptime": "5 days",
            "boot_time_label": "2024-07-11 08:00",
            "available": True,
        },
    )
    @mock.patch("core.system._load_shutdown_periods", return_value=([], None))
    @mock.patch("core.system.timezone.now")
    def test_displays_suite_uptime_summary(self, mock_now, mock_load_shutdowns, mock_suite_details):
        mock_now.return_value = datetime(2024, 7, 16, 12, 0, tzinfo=datetime_timezone.utc)

        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:system-uptime-report"))

        self.assertContains(response, "Suite uptime")
        self.assertContains(response, "5 days")
        self.assertContains(response, "2024-07-11 08:00")

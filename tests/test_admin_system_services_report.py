import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class AdminSystemServicesReportTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )

    def test_displays_configured_service_statuses(self):
        self.client.force_login(self.superuser)

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            locks = base_dir / "locks"
            locks.mkdir()
            locks.joinpath("service.lck").write_text("demo", encoding="utf-8")
            locks.joinpath("celery.lck").write_text("enabled", encoding="utf-8")
            locks.joinpath("lcd_screen.lck").write_text("1", encoding="utf-8")
            locks.joinpath("auto_upgrade.lck").write_text("latest", encoding="utf-8")

            def run_side_effect(args, **kwargs):
                action = args[1]
                unit = args[2] if len(args) > 2 else ""
                if action == "is-active":
                    state, code, stderr = {
                        "demo": ("active\n", 0, ""),
                        "demo-auto-upgrade": ("inactive\n", 3, ""),
                        "celery-demo": ("active\n", 0, ""),
                        "celery-beat-demo": ("failed\n", 3, ""),
                        "lcd-demo": ("", 4, "Unit lcd-demo.service could not be found."),
                    }.get(unit, ("unknown\n", 3, ""))
                    return subprocess.CompletedProcess(
                        args, code, stdout=state, stderr=stderr
                    )
                if action == "is-enabled":
                    return subprocess.CompletedProcess(
                        args, 0, stdout="enabled\n", stderr=""
                    )
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            with self.settings(BASE_DIR=str(base_dir)):
                with mock.patch("core.system._systemctl_command", return_value=["systemctl"]):
                    with mock.patch("core.system.subprocess.run") as mock_run:
                        mock_run.side_effect = run_side_effect
                        response = self.client.get(
                            reverse("admin:system-services-report")
                        )

        self.assertContains(response, "Suite Services Report")
        self.assertContains(response, "demo.service")
        self.assertContains(response, "demo-auto-upgrade.service")
        self.assertContains(response, "celery-demo.service")
        self.assertContains(response, "celery-beat-demo.service")
        self.assertContains(response, "LCD screen")
        self.assertContains(response, "inactive")
        self.assertContains(response, "failed")
        self.assertContains(response, "Not found")

    @mock.patch("core.system._systemctl_command", return_value=[])
    def test_reports_missing_systemctl(self, mock_command):
        self.client.force_login(self.superuser)

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            locks = base_dir / "locks"
            locks.mkdir()
            locks.joinpath("service.lck").write_text("demo", encoding="utf-8")

            with self.settings(BASE_DIR=str(base_dir)):
                response = self.client.get(reverse("admin:system-services-report"))

        self.assertContains(
            response,
            "systemctl is not available on this node; service status may be incomplete.",
        )
        self.assertContains(response, "demo.service")
        self.assertContains(response, "Unavailable")

    def test_uses_recorded_systemd_units_when_present(self):
        self.client.force_login(self.superuser)

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            locks = base_dir / "locks"
            locks.mkdir()
            locks.joinpath("service.lck").write_text("demo", encoding="utf-8")
            locks.joinpath("systemd_services.lck").write_text(
                "demo.service\nextra-worker.service\n", encoding="utf-8"
            )

            def run_side_effect(args, **kwargs):
                action = args[1]
                unit = args[2] if len(args) > 2 else ""
                if action == "is-active":
                    state, code, stderr = {
                        "demo.service": ("active\n", 0, ""),
                        "extra-worker.service": ("failed\n", 3, ""),
                    }.get(unit, ("unknown\n", 3, ""))
                    return subprocess.CompletedProcess(
                        args, code, stdout=state, stderr=stderr
                    )
                if action == "is-enabled":
                    return subprocess.CompletedProcess(
                        args, 0, stdout="enabled\n", stderr=""
                    )
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            with self.settings(BASE_DIR=str(base_dir)):
                with mock.patch("core.system._systemctl_command", return_value=["systemctl"]):
                    with mock.patch("core.system.subprocess.run") as mock_run:
                        mock_run.side_effect = run_side_effect
                        response = self.client.get(
                            reverse("admin:system-services-report")
                        )

        self.assertContains(response, "Suite service")
        self.assertContains(response, "demo.service")
        self.assertContains(response, "extra-worker.service")
        self.assertContains(response, "failed")

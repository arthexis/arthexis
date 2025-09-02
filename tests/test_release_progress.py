import os
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import Client, TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from core.models import Package, PackageRelease


class ReleaseProgressTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin, _ = User.all_objects.get_or_create(
            username="admin",
            defaults={"email": "a@example.com", "is_superuser": True, "is_staff": True},
        )
        self.admin.set_password("pw")
        self.admin.save()
        self.client = Client()
        self.client.force_login(self.admin)
        self.package = Package.objects.create(name="pkg")
        self.version_path = Path("VERSION")
        self.original_version = self.version_path.read_text()
        self.addCleanup(lambda: self.version_path.write_text(self.original_version))

    def test_publish_progress_creates_log(self):
        release = PackageRelease.objects.create(package=self.package, version="1.0.0")
        url = reverse("release-progress", args=[release.pk, "publish"])
        commit_hash = "abcdef1234567890"

        def run_side_effect(cmd, check=True, capture_output=False, text=False):
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch("core.views.release_utils.promote", return_value=(commit_hash, "branch", "main")), \
             patch("core.views.release_utils.publish") as pub, \
             patch("core.views.requests.get") as req_get, \
             patch("core.views.subprocess.run", side_effect=run_side_effect):
            req_get.return_value.ok = True
            req_get.return_value.json.return_value = {"releases": {}}
            resp = self.client.get(url)
            for i in range(3):
                resp = self.client.get(f"{url}?step={i}")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "All steps completed")
        self.assertContains(
            resp,
            '<a href="https://pypi.org/project/pkg/1.0.0/" target="_blank" rel="noopener">https://pypi.org/project/pkg/1.0.0/</a>',
            html=True,
        )
        release.refresh_from_db()
        self.assertTrue(release.is_published)
        self.assertTrue(release.pypi_url)
        pub.assert_called_once()
        log_path = Path("logs") / f"pkg-1.0.0-{commit_hash[:7]}.log"
        self.assertTrue(log_path.exists())

    def test_publish_progress_logs_without_pr_step(self):
        release = PackageRelease.objects.create(package=self.package, version="1.1.0")
        url = reverse("release-progress", args=[release.pk, "publish"])
        commit_hash = "1234567890abcdef"

        def run_side_effect(cmd, check=True, capture_output=False, text=False):
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch("core.views.release_utils.promote", return_value=(commit_hash, "branch", "main")), \
             patch("core.views.release_utils.publish") as pub, \
             patch("core.views.requests.get") as req_get, \
             patch("core.views.subprocess.run", side_effect=run_side_effect):
            req_get.return_value.ok = True
            req_get.return_value.json.return_value = {"releases": {}}
            resp = self.client.get(url)
            for i in range(3):
                resp = self.client.get(f"{url}?step={i}")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "All steps completed")
        self.assertIsNone(resp.context.get("pr_url"))
        self.assertContains(
            resp,
            '<a href="https://pypi.org/project/pkg/1.1.0/" target="_blank" rel="noopener">https://pypi.org/project/pkg/1.1.0/</a>',
            html=True,
        )
        release.refresh_from_db()
        self.assertTrue(release.is_published)
        pub.assert_called_once()
        log_path = Path("logs") / f"pkg-1.1.0-{commit_hash[:7]}.log"
        self.assertTrue(log_path.exists())
        self.assertNotIn("PR", log_path.read_text())

    def test_publish_progress_breadcrumbs(self):
        release = PackageRelease.objects.create(package=self.package, version="3.0.0")
        url = reverse("release-progress", args=[release.pk, "publish"])
        resp = self.client.get(url)
        app_url = reverse("admin:app_list", args=("core",))
        self.assertContains(resp, f'<a href="{app_url}">Business Models</a>')
        list_url = reverse("admin:core_packagerelease_changelist")
        self.assertContains(resp, f'<a href="{list_url}">Package Releases</a>')

    def test_check_step_writes_version_file(self):
        release = PackageRelease.objects.create(package=self.package, version="2.0.0")
        url = reverse("release-progress", args=[release.pk, "publish"])
        self.client.get(url)
        with patch("core.views.release_utils.network_available", return_value=False):
            resp = self.client.get(f"{url}?step=0")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.version_path.read_text().strip(), "2.0.0")

    def test_check_step_fails_when_version_goes_backwards(self):
        self.version_path.write_text("3.0.0\n")
        release = PackageRelease.objects.create(package=self.package, version="2.0.0")
        url = reverse("release-progress", args=[release.pk, "publish"])
        self.client.get(url)
        with patch("core.views.release_utils.network_available", return_value=False):
            resp = self.client.get(f"{url}?step=0")
        self.assertEqual(resp.context["error"], "Version 2.0.0 is older than existing 3.0.0")
        self.assertEqual(self.version_path.read_text().strip(), "3.0.0")

    def test_session_resets_when_version_changes(self):
        release = PackageRelease.objects.create(package=self.package, version="1.2.0")
        url = reverse("release-progress", args=[release.pk, "publish"])
        self.client.get(url)
        with patch("core.views.release_utils.network_available", return_value=False):
            self.client.get(f"{url}?step=0")

        release.version = "1.3.0"
        release.save()

        resp = self.client.get(url)
        expected_log = f"logs/pkg-1.3.0-{release.revision[:7]}.log"
        self.assertEqual(resp.context["log_path"], expected_log)
        self.assertEqual(resp.context["current_step"], 0)

        with patch("core.views.release_utils.network_available", return_value=False):
            self.client.get(f"{url}?step=0")

        log_content = Path(expected_log).read_text()
        self.assertIn("Checking if version 1.3.0 exists on PyPI", log_content)
        self.assertNotIn("1.2.0", log_content)

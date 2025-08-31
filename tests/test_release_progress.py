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
        self.admin, _ = User.objects.get_or_create(
            username="admin",
            defaults={"email": "a@example.com", "is_superuser": True, "is_staff": True},
        )
        self.admin.set_password("pw")
        self.admin.save()
        self.client = Client()
        self.client.force_login(self.admin)
        self.package = Package.objects.create(name="pkg")

    def test_promote_progress_creates_log(self):
        release = PackageRelease.objects.create(package=self.package, version="1.0.0")
        url = reverse("release-progress", args=[release.pk, "promote"])
        commit_hash = "abcdef1234567890"

        def run_side_effect(cmd, check=True, capture_output=False, text=False):
            stdout = "http://example.com/pr/1\n" if cmd[:3] == ["/usr/bin/gh", "pr", "create"] else ""
            return subprocess.CompletedProcess(cmd, 0, stdout, "")

        with patch("core.views.release_utils.promote", return_value=(commit_hash, "branch", "main")), \
             patch("core.views.serializers.serialize", return_value="[]"), \
             patch("core.views.shutil.which", return_value="/usr/bin/gh"), \
             patch("core.views.subprocess.run", side_effect=run_side_effect):
            resp = self.client.get(url)
            for i in range(3):
                resp = self.client.get(f"{url}?step={i}")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "All steps completed")
        self.assertEqual(resp.context["pr_url"], "http://example.com/pr/1")
        log_path = Path("logs") / f"pkg-1.0.0-{commit_hash[:7]}.log"
        self.assertTrue(log_path.exists())
        release.refresh_from_db()
        self.assertTrue(release.is_promoted)

    def test_promote_progress_without_gh_skips_pr(self):
        release = PackageRelease.objects.create(package=self.package, version="1.1.0")
        url = reverse("release-progress", args=[release.pk, "promote"])
        commit_hash = "1234567890abcdef"

        def run_side_effect(cmd, check=True, capture_output=False, text=False):
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch("core.views.release_utils.promote", return_value=(commit_hash, "branch", "main")), \
             patch("core.views.serializers.serialize", return_value="[]"), \
             patch("core.views.shutil.which", return_value=None), \
             patch("core.views.subprocess.run", side_effect=run_side_effect):
            resp = self.client.get(url)
            for i in range(3):
                resp = self.client.get(f"{url}?step={i}")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "All steps completed")
        self.assertIsNone(resp.context["pr_url"])
        log_path = Path("logs") / f"pkg-1.1.0-{commit_hash[:7]}.log"
        self.assertTrue(log_path.exists())
        self.assertIn(
            "PR creation skipped",
            log_path.read_text(),
        )

    def test_promote_progress_breadcrumbs(self):
        release = PackageRelease.objects.create(package=self.package, version="3.0.0")
        url = reverse("release-progress", args=[release.pk, "promote"])
        resp = self.client.get(url)
        app_url = reverse("admin:app_list", args=("core",))
        self.assertContains(resp, f'<a href="{app_url}">Business Models</a>')
        list_url = reverse("admin:core_packagerelease_changelist")
        self.assertContains(resp, f'<a href="{list_url}">Package Releases</a>')

    def test_publish_progress_creates_log(self):
        release = PackageRelease.objects.create(
            package=self.package,
            version="2.0.0",
            revision="1234567abcdef",
            is_certified=True,
        )
        url = reverse("release-progress", args=[release.pk, "publish"])
        with patch("core.views.release_utils.publish") as pub:
            resp = self.client.get(url)
            resp = self.client.get(f"{url}?step=0")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "All steps completed")
        log_path = Path("logs") / f"pkg-2.0.0-{release.revision[:7]}.log"
        self.assertTrue(log_path.exists())
        pub.assert_called_once()
        release.refresh_from_db()

import os
import sys
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
        self.admin = User.objects.create_superuser("admin", "a@example.com", "pw")
        self.client = Client()
        self.client.force_login(self.admin)
        self.package = Package.objects.create(name="pkg")

    def test_promote_progress_creates_log(self):
        release = PackageRelease.objects.create(package=self.package, version="1.0.0")
        url = reverse("release-progress", args=[release.pk, "promote"])
        commit_hash = "abcdef1234567890"
        with patch("core.views.release_utils.promote", return_value=(commit_hash, "branch", "main")), \
             patch("core.views.call_command"), \
             patch("core.views.subprocess.run"):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302)
            resp = self.client.get(resp["Location"])
            self.assertEqual(resp.status_code, 302)
            resp = self.client.get(resp["Location"])
        self.assertContains(resp, "All steps completed")
        log_path = Path("logs") / f"pkg-1.0.0-{commit_hash[:7]}.log"
        self.assertTrue(log_path.exists())
        release.refresh_from_db()
        self.assertTrue(release.is_promoted)

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
            self.assertEqual(resp.status_code, 302)
            resp = self.client.get(resp["Location"])
        self.assertContains(resp, "All steps completed")
        log_path = Path("logs") / f"pkg-2.0.0-{release.revision[:7]}.log"
        self.assertTrue(log_path.exists())
        pub.assert_called_once()
        release.refresh_from_db()
        self.assertTrue(release.is_published)

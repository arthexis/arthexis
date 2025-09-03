import os
import sys
from pathlib import Path
import shutil
import subprocess
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from core.models import Package, PackageRelease
from utils import revision


class ReleaseProgressViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        User.all_objects.filter(username="admin").delete()
        self.user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )
        self.client.force_login(self.user)
        self.package = Package.objects.create(name="pkg")
        self.release = PackageRelease.objects.create(
            package=self.package,
            version="1.0",
            revision=revision.get_revision(),
        )
        self.log_dir = Path("logs")
        self.log_dir.mkdir(exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.log_dir, ignore_errors=True)

    def test_stale_log_removed_on_start(self):
        log_path = self.log_dir / (
            f"{self.package.name}-{self.release.version}-{self.release.revision[:7]}.log"
        )
        log_path.write_text("old data")

        url = reverse("release-progress", args=[self.release.pk, "publish"])
        response = self.client.get(url)

        self.assertEqual(response.context["log_content"], "")
        self.assertFalse(log_path.exists())

    @mock.patch("core.views.release_utils._git_clean", return_value=False)
    @mock.patch("core.views.release_utils.network_available", return_value=False)
    def test_dirty_fixtures_committed(self, net, git_clean):
        fixture_path = Path("core/fixtures/releases.json")
        original = fixture_path.read_text(encoding="utf-8")
        fixture_path.write_text("[]", encoding="utf-8")
        self.addCleanup(lambda: fixture_path.write_text(original, encoding="utf-8"))

        def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=" M core/fixtures/releases.json\n", stderr=""
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch("core.views.subprocess.run", side_effect=fake_run) as run:
            url = reverse("release-progress", args=[self.release.pk, "publish"])
            response = self.client.get(f"{url}?step=0")

        self.assertContains(response, "core/fixtures/releases.json")
        run.assert_any_call(
            ["git", "add", "core/fixtures/releases.json"], check=True
        )
        run.assert_any_call(
            ["git", "commit", "-m", "chore: update fixtures"], check=True
        )

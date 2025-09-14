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
from core.models import Package, PackageRelease, Todo


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
            revision="",
        )
        self.log_dir = Path("logs")
        self.log_dir.mkdir(exist_ok=True)
        lock_path = Path("locks") / f"release_publish_{self.release.pk}.json"
        if lock_path.exists():
            lock_path.unlink()
        Todo.objects.all().delete()

    def tearDown(self):
        shutil.rmtree(self.log_dir, ignore_errors=True)

    def test_stale_log_removed_on_start(self):
        log_path = self.log_dir / (f"{self.package.name}-{self.release.version}.log")
        log_path.write_text("old data")

        url = reverse("release-progress", args=[self.release.pk, "publish"])
        response = self.client.get(url)

        self.assertEqual(response.context["log_content"], "")
        self.assertFalse(log_path.exists())

    @mock.patch("core.views.release_utils._git_clean", return_value=False)
    @mock.patch("core.views.release_utils.network_available", return_value=False)
    def test_dirty_fixtures_committed(self, net, git_clean):
        fixture_path = Path("core/fixtures/releases__packagerelease_0_1_3.json")
        original = fixture_path.read_text(encoding="utf-8")
        fixture_path.write_text("[]", encoding="utf-8")
        self.addCleanup(lambda: fixture_path.write_text(original, encoding="utf-8"))

        def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=f" M {fixture_path}\n", stderr=""
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch("core.views.subprocess.run", side_effect=fake_run) as run:
            url = reverse("release-progress", args=[self.release.pk, "publish"])
            self.client.get(f"{url}?step=0")
            response = self.client.get(f"{url}?step=1")

        self.assertContains(response, str(fixture_path))
        run.assert_any_call(["git", "add", str(fixture_path)], check=True)
        run.assert_any_call(
            ["git", "commit", "-m", "chore: update fixtures"], check=True
        )

    def test_todos_block_release(self):
        Todo.objects.create(description="Do something", url="/admin/")
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        response = self.client.get(f"{url}?step=0")
        self.assertContains(response, "Resolve open TODO items")
        self.assertContains(response, "Do something")
        self.assertContains(
            response,
            '<a href="/admin/" target="_blank" rel="noopener">Do something</a>',
            html=True,
        )

    def test_abort_publish_stops_process(self):
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        self.client.get(url)
        lock_path = Path("locks") / f"release_publish_{self.release.pk}.json"
        self.assertTrue(lock_path.exists())

        response = self.client.get(f"{url}?abort=1")
        self.assertContains(response, "Publish aborted")
        self.assertIsNone(response.context["next_step"])
        self.assertFalse(lock_path.exists())

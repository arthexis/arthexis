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
        self.client = self.client_class()
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

    @mock.patch("core.views.release_utils._git_clean", return_value=True)
    def test_stale_log_removed_on_start(self, git_clean):
        log_path = self.log_dir / (f"{self.package.name}-{self.release.version}.log")
        log_path.write_text("old data")

        url = reverse("release-progress", args=[self.release.pk, "publish"])
        response = self.client.get(url)

        self.assertTrue(log_path.exists())

        response = self.client.get(f"{url}?start=1&step=0")

        self.assertTrue(log_path.exists())
        self.assertNotIn("old data", response.context["log_content"])

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

        with mock.patch("core.views.subprocess.run", side_effect=fake_run):
            url = reverse("release-progress", args=[self.release.pk, "publish"])
            self.client.get(f"{url}?start=1&step=0")
            response = self.client.get(f"{url}?step=1")
        self.assertEqual(response.status_code, 200)

    def test_todos_must_be_acknowledged(self):
        todo = Todo.objects.create(request="Do something", url="/admin/")
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 1,
            "log": f"{self.package.name}-{self.release.version}.log",
            "started": True,
        }
        session.save()
        response = self.client.get(f"{url}?step=1")
        self.assertEqual(
            response.context["todos"],
            [
                {
                    "id": todo.pk,
                    "request": "Do something",
                    "url": "/admin/",
                    "request_details": "",
                }
            ],
        )
        self.assertIsNone(response.context["next_step"])
        tmp_dir = Path("tmp_todos")
        tmp_dir.mkdir(exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))
        fx = tmp_dir / f"todos__{todo.pk}.json"
        fx.write_text("[]", encoding="utf-8")
        with (
            mock.patch("core.views.TODO_FIXTURE_DIR", tmp_dir),
            mock.patch("core.views.subprocess.run"),
        ):
            self.client.get(f"{url}?ack_todos=1")
            response = self.client.get(f"{url}?step=1")
        self.assertFalse(Todo.objects.filter(is_deleted=False).exists())
        self.assertFalse(fx.exists())
        self.assertIsNone(response.context.get("todos"))
        self.assertEqual(response.context["next_step"], 2)

    def test_abort_publish_stops_process(self):
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        self.client.get(f"{url}?start=1&step=0")
        lock_path = Path("locks") / f"release_publish_{self.release.pk}.json"
        self.assertTrue(lock_path.exists())

        response = self.client.get(f"{url}?abort=1")
        self.assertContains(response, "Publish aborted")
        self.assertIsNone(response.context["next_step"])
        self.assertFalse(lock_path.exists())

    @mock.patch("core.views.release_utils._git_clean", return_value=True)
    @mock.patch("core.views.release_utils.network_available", return_value=False)
    def test_pre_release_commit(self, net, git_clean):
        original = Path("VERSION").read_text(encoding="utf-8")
        self.addCleanup(lambda: Path("VERSION").write_text(original, encoding="utf-8"))

        def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 4,
            "log": f"{self.package.name}-{self.release.version}.log",
            "started": True,
        }
        session.save()

        with mock.patch("core.views.subprocess.run", side_effect=fake_run):
            url = reverse("release-progress", args=[self.release.pk, "publish"])
            response = self.client.get(f"{url}?step=4")

        self.assertEqual(
            Path("VERSION").read_text(encoding="utf-8").strip(),
            self.release.version,
        )
        self.assertIn("Execute pre-release actions", response.context["log_content"])

    def test_todo_done_marks_timestamp(self):
        todo = Todo.objects.create(request="Task")
        url = reverse("todo-done", args=[todo.pk])
        tmp_dir = Path("tmp_todos2")
        tmp_dir.mkdir(exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))
        fx = tmp_dir / f"todos__{todo.pk}.json"
        fx.write_text("[]", encoding="utf-8")
        with mock.patch("core.views.TODO_FIXTURE_DIR", tmp_dir):
            response = self.client.post(url)
        self.assertRedirects(response, reverse("admin:index"))
        todo.refresh_from_db()
        self.assertIsNotNone(todo.done_on)
        self.assertTrue(fx.exists())

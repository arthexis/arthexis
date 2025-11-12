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

from django.conf import settings
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from core.models import Package, PackageRelease, Todo, ReleaseManager
from core import views as core_views


class ReleaseProgressViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        User.all_objects.filter(username="admin").delete()
        self.user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )
        self.client = self.client_class()
        self.client.force_login(self.user)
        self.package = Package.objects.create(name="pkg", is_active=True)
        self.release = PackageRelease.objects.create(
            package=self.package,
            version="1.0",
            revision="",
        )
        self.log_name = core_views._release_log_name(
            self.package.name, self.release.version
        )
        self.version_path = Path("VERSION")
        self.original_version = self.version_path.read_text(encoding="utf-8")
        self.version_path.write_text(self.release.version, encoding="utf-8")
        self.addCleanup(
            lambda: self.version_path.write_text(
                self.original_version, encoding="utf-8"
            )
        )
        self.log_dir = Path(settings.LOG_DIR)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        lock_path = Path("locks") / f"release_publish_{self.release.pk}.json"
        if lock_path.exists():
            lock_path.unlink()
        Todo.objects.all().delete()

    def tearDown(self):
        shutil.rmtree(self.log_dir, ignore_errors=True)

    def _assign_release_manager(self):
        manager = ReleaseManager.objects.create(
            user=self.user,
            pypi_token="pypi-test-token",
        )
        self.release.release_manager = manager
        self.release.save(update_fields=["release_manager"])
        return manager

    @mock.patch("core.views.release_utils._git_clean", return_value=True)
    def test_stale_log_removed_on_start(self, git_clean):
        log_path = self.log_dir / self.log_name
        log_path.write_text("old data")

        url = reverse("release-progress", args=[self.release.pk, "publish"])
        response = self.client.get(url)

        self.assertTrue(log_path.exists())

        response = self.client.get(f"{url}?start=1&step=0")

        self.assertTrue(log_path.exists())
        self.assertNotIn("old data", response.context["log_content"])

    def test_log_hidden_before_start(self):
        log_path = self.log_dir / self.log_name
        log_path.write_text("old data")

        url = reverse("release-progress", args=[self.release.pk, "publish"])
        response = self.client.get(url)

        self.assertEqual(response.context["log_content"], "")

    def test_non_current_release_becomes_current(self):
        other = PackageRelease.objects.create(
            package=self.package, version="2.0", revision=""
        )
        url = reverse("release-progress", args=[other.pk, "publish"])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        other.refresh_from_db()
        self.assertEqual(
            self.version_path.read_text(encoding="utf-8").strip(), other.version
        )

    def test_release_sync_updates_version_file_and_package(self):
        self.package.is_active = False
        self.package.save(update_fields=["is_active"])
        self.release.version = "1.1"
        self.release.save(update_fields=["version"])
        self.version_path.write_text("1.0", encoding="utf-8")

        url = reverse("release-progress", args=[self.release.pk, "publish"])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.version_path.read_text(encoding="utf-8").strip(),
            self.release.version,
        )
        self.release.refresh_from_db()
        self.assertTrue(self.release.package.is_active)

    @mock.patch("core.views.release_utils._git_clean", return_value=False)
    @mock.patch("core.views.release_utils.network_available", return_value=False)
    def test_dirty_fixtures_committed(self, net, git_clean):
        fixture_path = Path("core/fixtures/releases__packagerelease_0_1_3.json")
        if fixture_path.exists():
            original = fixture_path.read_text(encoding="utf-8")
            self.addCleanup(
                lambda original=original: fixture_path.write_text(
                    original, encoding="utf-8"
                )
            )
        else:
            fixture_path.write_text("[]", encoding="utf-8")
            self.addCleanup(lambda: fixture_path.unlink(missing_ok=True))
        fixture_path.write_text("[]", encoding="utf-8")

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

    @mock.patch("core.views.release_utils._git_clean", return_value=False)
    @mock.patch("core.views.release_utils.network_available", return_value=False)
    def test_dirty_version_committed(self, net, git_clean):
        commands: list[list[str]] = []

        def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
            commands.append(cmd)
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=" M VERSION\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch("core.views.subprocess.run", side_effect=fake_run):
            url = reverse("release-progress", args=[self.release.pk, "publish"])
            self.client.get(f"{url}?start=1&step=0")
        response = self.client.get(f"{url}?step=1")

        self.assertEqual(response.status_code, 200)
        self.assertIn(["git", "add", "VERSION"], commands)
        self.assertIn(["git", "commit", "-m", "chore: update version"], commands)

    @mock.patch("core.views.release_utils._git_clean", return_value=True)
    @mock.patch("core.views.release_utils.network_available", return_value=False)
    def test_major_minor_version_change_purges_todos(self, net, git_clean):
        todo = Todo.objects.create(request="Legacy task", is_seed_data=True)
        self.release.version = "1.0.0"
        setattr(self.release, "_repo_version_before_sync", "0.9.9")
        self.version_path.write_text("0.9.9", encoding="utf-8")

        log_path = self.log_dir / self.log_name
        ctx: dict[str, object] = {}

        with mock.patch("core.views._sync_with_origin_main") as sync_main:
            sync_main.return_value = None
            with mock.patch("core.views.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(["git"], 0)
                core_views._step_check_version(self.release, ctx, log_path)

        self.assertFalse(Todo.objects.filter(pk=todo.pk).exists())
        self.assertIn(
            self.release.version,
            ctx.get("todo_purged_versions", []),
        )
        self.assertTrue(log_path.exists())
        log_content = log_path.read_text(encoding="utf-8")
        self.assertIn(
            "Removed 1 TODO (version change from 0.9.9 to 1.0.0)",
            log_content,
        )

    @mock.patch("core.views.PackageRelease.dump_fixture")
    def test_publish_step_records_warning_and_completes(self, dump_fixture):
        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 8,
            "log": self.log_name,
            "started": True,
            "todos_ack": True,
        }
        session.save()

        warning = core_views.release_utils.PostPublishWarning(
            "Upload to PyPI completed, but git push failed",
            uploaded=["PyPI"],
            followups=["Push git tag v1.0 to origin after updating credentials."],
        )

        url = reverse("release-progress", args=[self.release.pk, "publish"])
        log_path = self.log_dir / self.log_name

        with (
            mock.patch.object(
                PackageRelease,
                "build_publish_targets",
                return_value=[core_views.release_utils.RepositoryTarget(name="PyPI")],
            ),
            mock.patch.object(
                PackageRelease,
                "to_package",
                return_value=core_views.release_utils.DEFAULT_PACKAGE,
            ),
            mock.patch.object(
                PackageRelease,
                "to_credentials",
                return_value=core_views.release_utils.Credentials(token="token"),
            ),
            mock.patch("core.views.release_utils.publish", side_effect=warning),
        ):
            response = self.client.get(f"{url}?step=8")

        self.assertEqual(response.status_code, 200)
        updated_session = self.client.session.get(session_key, {})
        self.assertEqual(updated_session.get("step"), 9)
        self.assertNotIn("error", updated_session)
        stored_warnings = updated_session.get("warnings", [])
        self.assertEqual(len(stored_warnings), 1)
        self.assertEqual(stored_warnings[0]["message"], str(warning))
        self.assertEqual(
            stored_warnings[0]["followups"],
            ["Push git tag v1.0 to origin after updating credentials."],
        )
        dump_fixture.assert_called_once()
        self.release.refresh_from_db()
        self.assertEqual(
            self.release.pypi_url,
            f"https://pypi.org/project/{self.release.package.name}/{self.release.version}/",
        )
        self.assertTrue(log_path.exists())
        log_content = log_path.read_text(encoding="utf-8")
        self.assertIn(str(warning), log_content)
        self.assertIn("Push git tag v1.0 to origin after updating credentials.", log_content)

    @mock.patch("core.views.release_utils.network_available", return_value=False)
    @mock.patch("core.views._collect_dirty_files")
    @mock.patch("core.views._sync_with_origin_main")
    @mock.patch("core.views.release_utils._git_clean", return_value=False)
    def test_fixture_commit_retries_sync(
        self, git_clean, sync_main, collect_dirty, net_available
    ):
        fixture_path = Path("core/fixtures/releases__packagerelease_0_1_3.json")
        if fixture_path.exists():
            original = fixture_path.read_text(encoding="utf-8")
            self.addCleanup(
                lambda original=original: fixture_path.write_text(
                    original, encoding="utf-8"
                )
            )
        else:
            fixture_path.write_text("[]", encoding="utf-8")
            self.addCleanup(lambda: fixture_path.unlink(missing_ok=True))
        fixture_path.write_text("[]", encoding="utf-8")

        collect_dirty.return_value = [
            {"path": str(fixture_path), "status": "M", "status_label": "Modified"}
        ]

        sync_main.side_effect = [Exception("rebase failed"), None]

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
        self.assertEqual(sync_main.call_count, 2)

    @mock.patch("core.views._collect_dirty_files")
    @mock.patch("core.views._sync_with_origin_main")
    @mock.patch("core.views.release_utils._git_clean", return_value=False)
    def test_dirty_repo_requires_resolution(self, git_clean, sync_main, collect):
        collect.return_value = [
            {"path": "core/models.py", "status": "M", "status_label": "Modified"}
        ]
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        self.client.get(f"{url}?start=1&step=0")
        response = self.client.get(f"{url}?step=0")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["dirty_files"],
            [
                {
                    "path": "core/models.py",
                    "status": "M",
                    "status_label": "Modified",
                }
            ],
        )
        self.assertIsNone(response.context["next_step"])

    @mock.patch("core.views._sync_with_origin_main")
    @mock.patch("core.views.release_utils._git_clean", return_value=True)
    @mock.patch("core.views.release_utils.network_available", return_value=True)
    @mock.patch("core.views.requests.get")
    def test_version_check_uses_timeout(
        self, requests_get, network_available, git_clean, sync_main
    ):
        requests_get.return_value = mock.Mock(ok=False)

        url = reverse("release-progress", args=[self.release.pk, "publish"])
        response = self.client.get(f"{url}?start=1&step=0")

        self.assertEqual(response.status_code, 200)
        requests_get.assert_called_once()
        self.assertEqual(
            requests_get.call_args.kwargs.get("timeout"),
            core_views.PYPI_REQUEST_TIMEOUT,
        )

    @mock.patch("core.views.subprocess.run")
    @mock.patch("core.views._collect_dirty_files")
    @mock.patch("core.views._sync_with_origin_main")
    @mock.patch("core.views.release_utils._git_clean")
    def test_dirty_repo_commit_action(
        self, git_clean, sync_main, collect, run
    ):
        git_clean.side_effect = [False, True]
        collect.side_effect = [
            [{"path": "core/models.py", "status": "M", "status_label": "Modified"}],
            [],
        ]

        def fake_run(cmd, check=False, capture_output=False, text=False, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        run.side_effect = fake_run
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        self.client.get(f"{url}?start=1&step=0")
        response = self.client.get(
            f"{url}?step=0&dirty_action=commit&dirty_message=Workspace%20cleanup"
        )
        self.assertEqual(response.status_code, 200)
        run.assert_any_call(["git", "add", "--all"], check=True)
        run.assert_any_call(
            ["git", "commit", "-m", "Workspace cleanup"], check=True
        )

    @mock.patch("core.views._append_log", wraps=core_views._append_log)
    @mock.patch("core.views._sync_with_origin_main")
    @mock.patch("core.views.select_log_dir")
    def test_release_log_dir_falls_back_when_unwritable(
        self, select_log_dir, sync_main, append_log
    ):
        fallback = Path("logs-fallback")
        fallback.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(fallback, ignore_errors=True))

        select_log_dir.return_value = fallback

        unwritable = Path("logs-unwritable")
        unwritable.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(unwritable, ignore_errors=True))

        url = reverse("release-progress", args=[self.release.pk, "publish"])
        with (
            self.settings(LOG_DIR=str(unwritable)),
            mock.patch(
                "core.views._ensure_log_directory",
                side_effect=[
                    (False, PermissionError("denied")),
                    (True, None),
                    (True, None),
                ],
            ),
        ):
            self.client.get(url)
            response = self.client.get(f"{url}?start=1&step=0")
            self.assertIsInstance(settings.LOG_DIR, Path)
            self.assertEqual(settings.LOG_DIR, fallback)

        self.assertEqual(response.status_code, 200)
        log_path = fallback / self.log_name
        self.assertTrue(log_path.exists())
        messages = [call.args[1] for call in append_log.call_args_list]
        self.assertIn(
            f"Release log directory {unwritable} is not writable; using {fallback}",
            messages,
        )

    @mock.patch("core.views._ensure_log_directory")
    @mock.patch("core.views.select_log_dir")
    def test_release_log_dir_restores_env_override(
        self, select_log_dir, ensure_log_directory
    ):
        override = Path("logs-env-override")
        override.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(override, ignore_errors=True))

        previous = os.environ.get("ARTHEXIS_LOG_DIR")
        os.environ["ARTHEXIS_LOG_DIR"] = str(override)

        def restore_env() -> None:
            if previous is None:
                os.environ.pop("ARTHEXIS_LOG_DIR", None)
            else:
                os.environ["ARTHEXIS_LOG_DIR"] = previous

        self.addCleanup(restore_env)

        original_log_dir = settings.LOG_DIR
        self.addCleanup(lambda: setattr(settings, "LOG_DIR", original_log_dir))

        select_log_dir.return_value = override
        ensure_log_directory.side_effect = [
            (False, PermissionError("denied")),
            (True, None),
        ]

        fallback, warning = core_views._resolve_release_log_dir(
            Path("logs-unwritable-env")
        )

        self.assertEqual(fallback, override)
        self.assertEqual(os.environ["ARTHEXIS_LOG_DIR"], str(override))
        self.assertIsNotNone(warning)

    @mock.patch("core.views._append_log", wraps=core_views._append_log)
    @mock.patch("core.views._sync_with_origin_main")
    @mock.patch("core.views.select_log_dir")
    def test_release_log_dir_warning_logged_once(
        self, select_log_dir, sync_main, append_log
    ):
        fallback = Path("logs-fallback-once")
        fallback.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(fallback, ignore_errors=True))

        select_log_dir.return_value = fallback

        unwritable = Path("logs-unwritable-once")
        unwritable.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(unwritable, ignore_errors=True))

        url = reverse("release-progress", args=[self.release.pk, "publish"])
        with (
            self.settings(LOG_DIR=str(unwritable)),
            mock.patch(
                "core.views._ensure_log_directory",
                side_effect=[
                    (False, PermissionError("denied")),
                    (True, None),
                    (True, None),
                    (True, None),
                ],
            ),
        ):
            self.client.get(url)
            self.client.get(f"{url}?start=1&step=0")
            self.client.get(url)

        messages = [call.args[1] for call in append_log.call_args_list]
        warning = (
            f"Release log directory {unwritable} is not writable; using {fallback}"
        )
        self.assertEqual(messages.count(warning), 1)

    @mock.patch("core.views._clean_repo")
    @mock.patch("core.views._collect_dirty_files")
    @mock.patch("core.views._sync_with_origin_main")
    @mock.patch("core.views.release_utils._git_clean")
    def test_dirty_repo_discard_action(
        self, git_clean, sync_main, collect, clean_repo
    ):
        git_clean.side_effect = [False, True]
        collect.side_effect = [
            [{"path": "core/models.py", "status": "M", "status_label": "Modified"}],
            [],
        ]
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        self.client.get(f"{url}?start=1&step=0")
        response = self.client.get(f"{url}?step=0&dirty_action=discard")
        self.assertEqual(response.status_code, 200)
        clean_repo.assert_called_once()
        self.assertFalse(response.context["dirty_files"])
        self.assertEqual(response.context["current_step"], 1)

    @mock.patch("core.views._refresh_changelog_once")
    def test_todos_must_be_acknowledged(self, refresh_changelog):
        refresh_changelog.side_effect = (
            lambda ctx, log_path: ctx.setdefault("changelog_refreshed", True)
        )
        todo = Todo.objects.create(request="Do something", url="/admin/")
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 1,
            "log": self.log_name,
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

    @mock.patch("core.views._refresh_changelog_once")
    def test_empty_todo_list_allows_progress(self, refresh_changelog):
        refresh_changelog.side_effect = (
            lambda ctx, log_path: ctx.setdefault("changelog_refreshed", True)
        )
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 1,
            "log": self.log_name,
            "started": True,
        }
        session.save()

        response = self.client.get(f"{url}?step=1")
        self.assertIsNone(response.context.get("todos"))
        self.assertFalse(response.context["has_pending_todos"])
        self.assertFalse(response.context["changelog_report_url"] == "")
        self.assertEqual(response.context["current_step"], 2)
        self.assertEqual(response.context["next_step"], 2)

    def test_publish_view_auto_acknowledges_empty_todos(self):
        url = reverse("release-progress", args=[self.release.pk, "publish"])

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        ctx = session.get(session_key, {})
        self.assertTrue(ctx.get("todos_ack"))
        self.assertIsNone(response.context["todos"])
        self.assertFalse(response.context["has_pending_todos"])

    def test_step_check_todos_refreshes_changelog_once(self):
        todo = Todo.objects.create(request="Review changelog", url="/admin/")
        ctx: dict[str, object] = {}
        log_path = self.log_dir / "refresh-once.log"
        commands: list[list[str]] = []

        def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
            commands.append(list(cmd))
            if cmd == ["git", "diff", "--cached", "--name-only"]:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="CHANGELOG.rst\n", stderr=""
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch("core.views.subprocess.run", side_effect=fake_run):
            with self.assertRaises(core_views.PendingTodos):
                core_views._step_check_todos(self.release, ctx, log_path)
            first_command_count = len(commands)
            with self.assertRaises(core_views.PendingTodos):
                core_views._step_check_todos(self.release, ctx, log_path)

        self.assertTrue(ctx.get("changelog_refreshed"))
        self.assertTrue(ctx.get("todos_required"))
        self.assertEqual(
            ctx.get("todos"),
            [
                {
                    "id": todo.pk,
                    "request": "Review changelog",
                    "url": "/admin/",
                    "request_details": "",
                }
            ],
        )
        self.assertEqual(len(commands), first_command_count)
        script_calls = [cmd for cmd in commands if cmd == ["scripts/generate-changelog.sh"]]
        self.assertEqual(len(script_calls), 1)
        commit_calls = [
            cmd for cmd in commands if cmd[:3] == ["git", "commit", "-m"]
        ]
        self.assertEqual(len(commit_calls), 1)

    def test_refresh_changelog_recovers_missing_latest_release(self):
        original = Path("CHANGELOG.rst").read_text(encoding="utf-8")
        latest_version = "0.1.31"
        stale_text = "\n".join(
            [
                "Changelog",
                "=========",
                "",
                "Unreleased",
                "----------",
                "",
                "- placeholder entry",
                "",
                "v0.1.22 (2025-10-28)",
                "--------------------",
                "",
                "- previous release entry",
                "",
            ]
        )
        Path("CHANGELOG.rst").write_text(stale_text, encoding="utf-8")
        self.addCleanup(
            lambda: Path("CHANGELOG.rst").write_text(original, encoding="utf-8")
        )

        ctx: dict[str, object] = {}
        log_path = self.log_dir / "missing-release.log"

        def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
            if cmd[:3] == ["git", "diff", "--cached"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="CHANGELOG.rst\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        def successful_fallback(log_path):
            core_views._append_log(log_path, "Falling back to Python changelog generator")
            title = f"v{latest_version} (2025-01-01)"
            underline = "-" * len(title)
            content = "\n".join(
                [
                    "Changelog",
                    "=========",
                    "",
                    "Unreleased",
                    "----------",
                    "",
                    "- unreleased placeholder",
                    "",
                    title,
                    underline,
                    "",
                    "- regenerated entry",
                    "",
                ]
            )
            Path("CHANGELOG.rst").write_text(f"{content}\n", encoding="utf-8")
            core_views._append_log(
                log_path, "Regenerated CHANGELOG.rst using Python fallback"
            )

        with mock.patch(
            "core.views.changelog_utils.latest_release_version_from_history",
            return_value=latest_version,
        ), mock.patch("core.views.subprocess.run", side_effect=fake_run), mock.patch(
            "core.views._generate_changelog_with_python", side_effect=successful_fallback
        ):
            core_views._refresh_changelog_once(ctx, log_path)

        self.assertTrue(ctx.get("changelog_refreshed"))
        self.assertTrue(log_path.exists())
        log_content = log_path.read_text(encoding="utf-8")
        if latest_version:
            self.assertIn(
                f"Changelog missing latest release v{latest_version}", log_content
            )
            self.assertIn("Python changelog generator", log_content)
            self.assertIn(
                f"Recovered changelog entry for v{latest_version} after fallback",
                log_content,
            )

    def test_refresh_changelog_missing_latest_release_requires_manual_fix(self):
        original = Path("CHANGELOG.rst").read_text(encoding="utf-8")
        latest_version = "0.1.31"
        stale_text = "\n".join(
            [
                "Changelog",
                "=========",
                "",
                "Unreleased",
                "----------",
                "",
                "- placeholder entry",
                "",
            ]
        )
        Path("CHANGELOG.rst").write_text(stale_text, encoding="utf-8")
        self.addCleanup(
            lambda: Path("CHANGELOG.rst").write_text(original, encoding="utf-8")
        )

        ctx: dict[str, object] = {}
        log_path = self.log_dir / "missing-release-instructions.log"

        def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
            if cmd[:3] == ["git", "diff", "--cached"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="CHANGELOG.rst\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        def stubborn_fallback(log_path):
            core_views._append_log(
                log_path, "Simulated fallback unable to populate changelog"
            )
            Path("CHANGELOG.rst").write_text(stale_text, encoding="utf-8")

        with mock.patch(
            "core.views.changelog_utils.latest_release_version_from_history",
            return_value=latest_version,
        ), mock.patch("core.views.subprocess.run", side_effect=fake_run), mock.patch(
            "core.views._generate_changelog_with_python", side_effect=stubborn_fallback
        ):
            with self.assertRaises(RuntimeError) as exc:
                core_views._refresh_changelog_once(ctx, log_path)

        self.assertFalse(ctx.get("changelog_refreshed"))
        self.assertIn("Ensure the release commit", str(exc.exception))
        self.assertTrue(log_path.exists())
        log_content = log_path.read_text(encoding="utf-8")
        if latest_version:
            self.assertIn(
                f"Changelog missing latest release v{latest_version}", log_content
            )
            self.assertIn("Manual changelog update required", log_content)

    @mock.patch("core.views._refresh_changelog_once")
    def test_acknowledged_todos_not_rendered(self, refresh_changelog):
        refresh_changelog.side_effect = (
            lambda ctx, log_path: ctx.setdefault("changelog_refreshed", True)
        )
        todo = Todo.objects.create(request="Do something", url="/admin/")
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 1,
            "log": self.log_name,
            "started": True,
            "todos": [
                {
                    "id": todo.pk,
                    "request": "Do something",
                    "url": "/admin/",
                    "request_details": "",
                }
            ],
            "todos_ack": True,
        }
        session.save()

        response = self.client.get(url)

        self.assertIsNone(response.context["todos"])
        self.assertFalse(response.context["has_pending_todos"])

    @mock.patch("core.views._refresh_changelog_once")
    def test_todo_ack_condition_failure_blocks_acknowledgement(
        self, refresh_changelog
    ):
        refresh_changelog.side_effect = (
            lambda ctx, log_path: ctx.setdefault("changelog_refreshed", True)
        )
        todo = Todo.objects.create(
            request="Do something",
            on_done_condition="1 = 0",
        )
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 1,
            "log": self.log_name,
            "started": True,
        }
        session.save()

        response = self.client.get(f"{url}?ack_todos=1")
        messages = [m.message for m in get_messages(response.wsgi_request)]
        self.assertTrue(messages)
        self.assertIn("1 = 0", messages[0])

        response = self.client.get(f"{url}?step=1")
        self.assertIsNone(response.context.get("next_step"))
        self.assertEqual(
            response.context["todos"],
            [
                {
                    "id": todo.pk,
                    "request": "Do something",
                    "url": "",
                    "request_details": "",
                }
            ],
        )

    def test_release_manager_approval_requires_input(self):
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 7,
            "log": self.log_name,
            "started": True,
            "todos_ack": True,
        }
        session.save()

        response = self.client.get(f"{url}?step=7")

        self.assertTrue(response.context["awaiting_approval"])
        self.assertIsNone(response.context["next_step"])
        self.assertFalse(response.context["approval_credentials_ready"])
        self.assertTrue(response.context["approval_credentials_missing"])
        self.assertIn(
            "Release manager publishing credentials missing",
            response.context["log_content"],
        )
        self.assertNotContains(response, 'name="approve"')
        self.assertContains(response, "Publishing credentials required")

    def test_release_manager_approval_uses_current_user_profile(self):
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        User = get_user_model()
        try:
            arthexis = User.all_objects.get(username="arthexis")
        except User.DoesNotExist:
            arthexis = User.objects.create_user(
                username="arthexis",
                email="arthexis@example.com",
                password="password123",
            )
        arthexis_manager = ReleaseManager.objects.filter(user=arthexis).first()
        if arthexis_manager is None:
            arthexis_manager = ReleaseManager.objects.create(user=arthexis)
        else:
            arthexis_manager.pypi_token = ""
            arthexis_manager.pypi_username = ""
            arthexis_manager.pypi_password = ""
            arthexis_manager.save(
                update_fields=["pypi_token", "pypi_username", "pypi_password"]
            )
        self.release.release_manager = arthexis_manager
        self.release.save(update_fields=["release_manager"])

        ReleaseManager.objects.create(user=self.user, pypi_token="user-token")

        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 7,
            "log": self.log_name,
            "started": True,
            "todos_ack": True,
        }
        session.save()

        response = self.client.get(f"{url}?step=7")

        self.assertTrue(response.context["approval_credentials_ready"])
        self.assertFalse(response.context["approval_credentials_missing"])
        self.assertNotIn(
            "Release manager publishing credentials missing",
            response.context["log_content"],
        )

    @mock.patch.dict(os.environ, {"PYPI_API_TOKEN": "token-from-env"}, clear=False)
    def test_release_manager_approval_uses_environment_credentials(self):
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 7,
            "log": self.log_name,
            "started": True,
            "todos_ack": True,
        }
        session.save()

        response = self.client.get(f"{url}?step=7")

        self.assertTrue(response.context["approval_credentials_ready"])
        self.assertFalse(response.context["approval_credentials_missing"])
        self.assertNotIn(
            "Release manager publishing credentials missing",
            response.context["log_content"],
        )

    def test_release_followup_todo_does_not_block_final_approval(self):
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        self._assign_release_manager()
        next_version = core_views._next_patch_version(self.release.version)
        Todo.objects.create(
            request=f"Create release {self.package.name} {next_version}",
            generated_for_version=self.release.version,
            generated_for_revision=self.release.revision,
            is_seed_data=True,
        )
        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 7,
            "log": self.log_name,
            "started": True,
            "todos_ack": True,
        }
        session.save()

        with mock.patch("config.context_processors.site_and_node", return_value={}):
            response = self.client.get(f"{url}?step=7")

        self.assertTrue(response.context["awaiting_approval"])
        self.assertTrue(response.context["approval_credentials_ready"])
        self.assertFalse(response.context["has_pending_todos"])
        self.assertIsNone(response.context["todos"])
        self.assertNotContains(response, "Pending TODOs")

    def test_release_manager_approval_accepts(self):
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        self._assign_release_manager()
        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 7,
            "log": self.log_name,
            "started": True,
            "todos_ack": True,
        }
        session.save()

        initial = self.client.get(f"{url}?step=7")
        self.assertTrue(initial.context["approval_credentials_ready"])
        response = self.client.get(f"{url}?approve=1&step=7")

        self.assertFalse(response.context["awaiting_approval"])
        self.assertEqual(response.context["current_step"], 8)
        self.assertEqual(response.context["next_step"], 8)
        self.assertIn(
            "Release manager approved release", response.context["log_content"]
        )

    def test_release_manager_rejection_aborts(self):
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        self._assign_release_manager()
        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 7,
            "log": self.log_name,
            "started": True,
            "todos_ack": True,
        }
        session.save()

        initial = self.client.get(f"{url}?step=7")
        self.assertTrue(initial.context["approval_credentials_ready"])
        response = self.client.get(f"{url}?reject=1&step=7")

        self.assertEqual(
            response.context["error"],
            "Release manager rejected the release. Restart required.",
        )
        self.assertFalse(response.context["awaiting_approval"])
        self.assertIsNone(response.context["next_step"])
        self.assertIn(
            "Release manager rejected release", response.context["log_content"]
        )

    @mock.patch("core.views._sync_with_origin_main")
    @mock.patch("core.views.release_utils.network_available", return_value=False)
    @mock.patch("core.views.release_utils._git_clean", return_value=True)
    def test_pause_publish_suspends_process(
        self, git_clean, net_available, sync_main
    ):
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        self.client.get(url)
        self.client.get(f"{url}?start=1&step=0")
        lock_path = Path("locks") / f"release_publish_{self.release.pk}.json"
        self.assertTrue(lock_path.exists())

        response = self.client.get(f"{url}?pause=1")
        self.assertTrue(response.context["paused"])
        self.assertIsNone(response.context["next_step"])
        self.assertTrue(lock_path.exists())
        self.assertContains(response, "Continue Publish")

    @mock.patch("core.views.release_utils._git_clean", return_value=True)
    @mock.patch("core.views.release_utils.network_available", return_value=False)
    def test_pre_release_defers_todo_fixture_until_build(self, net, git_clean):
        original = Path("VERSION").read_text(encoding="utf-8")
        self.addCleanup(lambda: Path("VERSION").write_text(original, encoding="utf-8"))

        commands: list[list[str]] = []
        fixture_filename = "todos__create_release_pkg_1_0_1.json"

        def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
            commands.append(cmd)
            if (
                cmd[:4] == ["git", "diff", "--cached", "--quiet"]
                and any(part.endswith(fixture_filename) for part in cmd)
            ):
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        tmp_dir = Path("tmp_todos_pre_release")
        tmp_dir.mkdir(exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))

        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 4,
            "log": self.log_name,
            "started": True,
            "todos_ack": True,
        }
        session.save()

        with mock.patch("core.views.TODO_FIXTURE_DIR", tmp_dir):
            with mock.patch(
                "core.views.changelog_utils.extract_release_notes",
                return_value="- pending change",
            ):
                with mock.patch("core.views.PackageRelease.dump_fixture"):
                    with mock.patch(
                        "core.views.subprocess.run", side_effect=fake_run
                    ):
                        url = reverse(
                            "release-progress", args=[self.release.pk, "publish"]
                        )
                        response = self.client.get(f"{url}?step=4")

        self.assertIn(["scripts/generate-changelog.sh"], commands)
        self.assertEqual(response.status_code, 200)
        expected_request = "Create release pkg 1.0.1"
        fixture_path = tmp_dir / fixture_filename
        self.assertFalse(Todo.objects.filter(request=expected_request).exists())
        self.assertFalse(fixture_path.exists())

        log_path = Path("logs") / self.log_name
        self.addCleanup(lambda: log_path.unlink(missing_ok=True))
        self.assertTrue(log_path.exists())
        log_content = log_path.read_text(encoding="utf-8")
        self.assertIn(
            "Regenerated CHANGELOG.rst using scripts/generate-changelog.sh",
            log_content,
        )
        self.assertIn("Staged CHANGELOG.rst for commit", log_content)
        self.assertNotIn(f"Added TODO: {expected_request}", log_content)
        self.assertIn("Recorded changelog notes for v1.0", log_content)
        self.assertEqual(
            Path("VERSION").read_text(encoding="utf-8").strip(),
            self.release.version,
        )
        self.assertIn("Execute pre-release actions", log_content)
        self.assertIn(
            f"Updated VERSION file to {self.release.version}", log_content
        )
        self.assertIn("Staged VERSION for commit", log_content)
        self.assertIn(
            "No changes detected for VERSION or CHANGELOG; skipping commit",
            log_content,
        )
        self.assertIn("Unstaged CHANGELOG.rst", log_content)
        self.assertIn("Unstaged VERSION file", log_content)
        self.assertNotIn(
            ["git", "commit", "-m", "chore: add release TODO for pkg"], commands
        )
        self.release.refresh_from_db()
        self.assertEqual(self.release.changelog, "- pending change")
        session = self.client.session
        ctx = session.get(session_key, {})
        self.assertNotIn("release_todo_previous_version", ctx)

    @mock.patch("core.views.release_utils._git_clean", return_value=True)
    @mock.patch("core.views.release_utils.network_available", return_value=False)
    def test_pre_release_python_fallback(self, net, git_clean):
        original_version = Path("VERSION").read_text(encoding="utf-8")
        original_changelog = Path("CHANGELOG.rst").read_text(encoding="utf-8")
        self.addCleanup(
            lambda: Path("VERSION").write_text(original_version, encoding="utf-8")
        )
        self.addCleanup(
            lambda: Path("CHANGELOG.rst").write_text(
                original_changelog, encoding="utf-8"
            )
        )

        commands: list[list[str]] = []
        changelog_entry = "- abcdef12 Fix fallback generation output (#42)"

        def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
            commands.append(cmd)
            if cmd == ["scripts/generate-changelog.sh"]:
                err = OSError(193, "%1 is not a valid Win32 application")
                err.winerror = 193  # type: ignore[attr-defined]
                raise err
            if cmd[:4] == ["git", "describe", "--tags", "--exact-match"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            if (
                len(cmd) >= 4
                and cmd[:4] == ["git", "describe", "--tags", "--abbrev=0"]
            ):
                return subprocess.CompletedProcess(cmd, 0, stdout="0.1.10\n", stderr="")
            if (
                len(cmd) >= 4
                and cmd[0] == "git"
                and cmd[1] == "log"
                and cmd[2] == "0.1.10..HEAD"
            ):
                commit = "abcdef1234567890"
                subject = "Fix fallback generation output (#42)"
                date = "2025-10-03"
                stdout = f"{commit}\x00{date}\x00{subject}\n"
                return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
            if (
                cmd[:3] == ["git", "diff", "--cached"]
                and any(part.endswith("CHANGELOG.rst") for part in cmd)
            ):
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        tmp_dir = Path("tmp_todos_pre_release_fallback")
        tmp_dir.mkdir(exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))

        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 4,
            "log": self.log_name,
            "started": True,
            "todos_ack": True,
        }
        session.save()

        with mock.patch("core.views.TODO_FIXTURE_DIR", tmp_dir):
            with mock.patch(
                "core.views.changelog_utils.extract_release_notes",
                return_value=changelog_entry,
            ):
                with mock.patch("core.views.PackageRelease.dump_fixture"):
                    with mock.patch(
                        "core.views.subprocess.run", side_effect=fake_run
                    ):
                        url = reverse(
                            "release-progress", args=[self.release.pk, "publish"]
                        )
                        response = self.client.get(f"{url}?step=4")

        self.assertIn(["scripts/generate-changelog.sh"], commands)
        self.assertEqual(response.status_code, 200)
        log_path = Path("logs") / self.log_name
        self.addCleanup(lambda: log_path.unlink(missing_ok=True))
        self.assertTrue(log_path.exists())
        log_content = log_path.read_text(encoding="utf-8")
        self.assertIn(
            "scripts/generate-changelog.sh failed: [Errno 193] %1 is not a valid Win32 application",
            log_content,
        )
        self.assertIn("Regenerated CHANGELOG.rst using Python fallback", log_content)
        self.assertIn("Staged CHANGELOG.rst for commit", log_content)
        self.assertEqual(
            Path("VERSION").read_text(encoding="utf-8").strip(),
            self.release.version,
        )
        self.assertIn(changelog_entry, Path("CHANGELOG.rst").read_text(encoding="utf-8"))
        self.assertFalse(
            Todo.objects.filter(request__icontains="Create release pkg").exists()
        )
        self.assertFalse(list(tmp_dir.glob("todos__*.json")))
        self.assertNotIn("Committed TODO fixture", log_content)
        self.assertIn("Recorded changelog notes for v1.0", log_content)
        self.release.refresh_from_db()
        self.assertEqual(self.release.changelog, changelog_entry)
        session = self.client.session
        ctx = session.get(session_key, {})
        self.assertNotIn("release_todo_previous_version", ctx)

    @mock.patch("core.views.release_utils.promote")
    @mock.patch("core.views.PackageRelease.dump_fixture")
    @mock.patch("core.views._ensure_origin_main_unchanged")
    def test_build_step_skips_todo_creation_on_success(
        self, ensure_main, dump_fixture, promote
    ):
        commands: list[list[str]] = []

        def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 5,
            "log": self.log_name,
            "started": True,
            "todos_ack": True,
        }
        session.save()

        with mock.patch("core.views._has_remote", return_value=False):
            with mock.patch("core.views.subprocess.run", side_effect=fake_run):
                url = reverse("release-progress", args=[self.release.pk, "publish"])
                response = self.client.get(f"{url}?step=5")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Todo.objects.exists())
        updated_session = self.client.session.get(session_key, {})
        self.assertEqual(updated_session.get("step"), 6)

    @mock.patch("core.views.release_utils.promote", side_effect=Exception("build failed"))
    @mock.patch("core.views.PackageRelease.dump_fixture")
    @mock.patch("core.views._ensure_origin_main_unchanged")
    def test_build_step_failure_keeps_state_without_todo(
        self, ensure_main, dump_fixture, promote
    ):
        commands: list[list[str]] = []

        def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        session = self.client.session
        session_key = f"release_publish_{self.release.pk}"
        session[session_key] = {
            "step": 5,
            "log": self.log_name,
            "started": True,
            "todos_ack": True,
        }
        session.save()

        with mock.patch("core.views._has_remote", return_value=False):
            with mock.patch("core.views.subprocess.run", side_effect=fake_run):
                url = reverse("release-progress", args=[self.release.pk, "publish"])
                response = self.client.get(f"{url}?step=5")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Todo.objects.exists())
        updated_session = self.client.session.get(session_key, {})
        self.assertEqual(updated_session.get("error"), "build failed")
        self.assertNotIn("release_todo_previous_version", updated_session)
        self.assertEqual(updated_session.get("step"), 5)

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


class SyncWithOriginMainTests(TestCase):
    def setUp(self):
        self.log_path = Path("logs") / "sync-with-origin-main.log"
        if self.log_path.exists():
            self.log_path.unlink()

    def tearDown(self):
        if self.log_path.exists():
            self.log_path.unlink()

    @mock.patch("core.views._has_remote", return_value=True)
    def test_auto_commits_todo_fixtures_before_rebase(self, has_remote):
        commands: list[list[str]] = []

        def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
            commands.append(list(cmd))
            if cmd[:3] == ["git", "status", "--porcelain"]:
                stdout = (
                    " M core/fixtures/todo__validate_admin.json\n"
                    "?? core/fixtures/todo__validate_dashboard.json\n"
                )
                return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
            if cmd[:4] == ["git", "diff", "--cached", "--name-only"]:
                stdout = (
                    "core/fixtures/todo__validate_admin.json\n"
                    "core/fixtures/todo__validate_dashboard.json\n"
                )
                return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
            if cmd[:2] == ["git", "add"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[:3] == ["git", "commit", "-m"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[:3] == ["git", "fetch", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[:2] == ["git", "rebase"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            raise AssertionError(f"Unexpected command: {cmd}")

        with mock.patch("core.views.subprocess.run", side_effect=fake_run):
            core_views._sync_with_origin_main(self.log_path)

        add_commands = [cmd for cmd in commands if cmd[:2] == ["git", "add"]]
        self.assertEqual(
            add_commands,
            [
                [
                    "git",
                    "add",
                    "core/fixtures/todo__validate_admin.json",
                    "core/fixtures/todo__validate_dashboard.json",
                ]
            ],
        )
        self.assertIn(
            ["git", "commit", "-m", "chore: update TODO fixtures"],
            commands,
        )
        fetch_index = commands.index(["git", "fetch", "origin", "main"])
        commit_index = commands.index(
            ["git", "commit", "-m", "chore: update TODO fixtures"]
        )
        self.assertLess(commit_index, fetch_index)

        log_text = self.log_path.read_text(encoding="utf-8")
        self.assertIn(
            "Staged TODO fixtures core/fixtures/todo__validate_admin.json, "
            "core/fixtures/todo__validate_dashboard.json",
            log_text,
        )
        self.assertIn("Committed TODO fixtures (chore: update TODO fixtures)", log_text)

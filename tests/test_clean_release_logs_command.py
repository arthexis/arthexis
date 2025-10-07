import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings

from core.models import Package, PackageRelease


class CleanReleaseLogsCommandTests(TestCase):
    def setUp(self):
        self.package = Package.objects.create(name="pkg", is_active=True)
        self.release = PackageRelease.objects.create(
            package=self.package,
            version="1.0",
            revision="",
        )
        self.addCleanup(lambda: shutil.rmtree("locks", ignore_errors=True))

    def test_requires_arguments(self):
        with self.assertRaises(CommandError):
            call_command("clean_release_logs")

    def test_clean_specific_release_removes_logs_and_locks(self):
        other_release = PackageRelease.objects.create(
            package=self.package,
            version="2.0",
            revision="",
        )

        with TemporaryDirectory() as tmp_dir, TemporaryDirectory() as base_tmp:
            log_dir = Path(tmp_dir)
            base_dir = Path(base_tmp)
            keep_file = log_dir / "server.log"
            keep_file.write_text("keep", encoding="utf-8")

            target_prefix = f"pr.{self.package.name}.v{self.release.version}"
            log_path = log_dir / f"{target_prefix}.log"
            extra_variant = log_dir / f"{target_prefix}.1.log"
            log_path.write_text("entry", encoding="utf-8")
            extra_variant.write_text("more", encoding="utf-8")

            other_log = log_dir / f"pr.{self.package.name}.v{other_release.version}.log"
            other_log.write_text("other", encoding="utf-8")

            lock_dir = base_dir / "locks"
            lock_dir.mkdir(exist_ok=True)
            lock_file = lock_dir / f"release_publish_{self.release.pk}.json"
            restart_file = lock_dir / f"release_publish_{self.release.pk}.restarts"
            other_lock = lock_dir / f"release_publish_{other_release.pk}.json"
            lock_file.write_text("{}", encoding="utf-8")
            restart_file.write_text("1", encoding="utf-8")
            other_lock.write_text("{}", encoding="utf-8")

            with override_settings(LOG_DIR=log_dir, BASE_DIR=base_dir):
                call_command(
                    "clean_release_logs",
                    f"{self.package.name}:{self.release.version}",
                )

            self.assertFalse(log_path.exists())
            self.assertFalse(extra_variant.exists())
            self.assertTrue(other_log.exists())
            self.assertTrue(keep_file.exists())
            self.assertFalse(lock_file.exists())
            self.assertFalse(restart_file.exists())
            self.assertTrue(other_lock.exists())

    def test_clean_all_removes_all_release_logs(self):
        release_two = PackageRelease.objects.create(
            package=self.package,
            version="3.0",
            revision="",
        )

        with TemporaryDirectory() as tmp_dir, TemporaryDirectory() as base_tmp:
            log_dir = Path(tmp_dir)
            base_dir = Path(base_tmp)
            retained = log_dir / "app.log"
            retained.write_text("retain", encoding="utf-8")

            for release in (self.release, release_two):
                log_file = log_dir / f"pr.{self.package.name}.v{release.version}.log"
                log_file.write_text("entry", encoding="utf-8")

            lock_dir = base_dir / "locks"
            lock_dir.mkdir(exist_ok=True)
            for release in (self.release, release_two):
                (lock_dir / f"release_publish_{release.pk}.json").write_text(
                    "{}", encoding="utf-8"
                )
                (lock_dir / f"release_publish_{release.pk}.restarts").write_text(
                    "1", encoding="utf-8"
                )

            with override_settings(LOG_DIR=log_dir, BASE_DIR=base_dir):
                call_command("clean_release_logs", "--all")

            self.assertTrue(retained.exists())
            self.assertFalse(list(log_dir.glob("pr.*.log")))
            self.assertFalse(list(lock_dir.glob("release_publish_*")))

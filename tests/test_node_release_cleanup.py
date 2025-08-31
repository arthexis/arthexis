import os
import sys
import subprocess
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase

from core.models import Package, PackageRelease
from nodes.apps import _ensure_release
from utils import revision


class EnsureReleaseTests(TestCase):
    def setUp(self):
        self._orig_release = os.environ.pop("RELEASE", None)

    def tearDown(self):
        if self._orig_release is not None:
            os.environ["RELEASE"] = self._orig_release
        else:
            os.environ.pop("RELEASE", None)

    def test_ensure_release_updates_and_cleans(self):
        package, _ = Package.objects.get_or_create(name="arthexis")
        stale = PackageRelease.objects.create(package=package, version="0.0.0")
        version = Path("VERSION").read_text().strip()
        release = PackageRelease.objects.get(package=package, version=version)
        release.revision = "old"
        release.save(update_fields=["revision"])

        _ensure_release()

        current_rev = revision.get_revision()
        release.refresh_from_db()
        self.assertEqual(release.revision, current_rev)
        self.assertFalse(PackageRelease.objects.filter(pk=stale.pk).exists())
        self.assertEqual(
            PackageRelease.objects.filter(package=package).count(), 1
        )

    def test_ensure_release_uses_env_var(self):
        package, _ = Package.objects.get_or_create(name="arthexis")
        PackageRelease.objects.all().delete()
        os.environ["RELEASE"] = "9.9.9"

        _ensure_release()

        release = PackageRelease.objects.get(package=package, version="9.9.9")
        self.assertEqual(release.revision, revision.get_revision())

    def test_ensure_release_marks_certified_and_published(self):
        package, _ = Package.objects.get_or_create(name="arthexis")
        version = Path("VERSION").read_text().strip()
        release = PackageRelease.objects.get(package=package, version=version)
        release.is_certified = False
        release.is_published = False
        release.save(update_fields=["is_certified", "is_published"])

        def fake_json():
            return {"releases": {version: []}}

        with patch("nodes.apps.subprocess.run") as run, \
             patch("requests.get") as req:
            run.return_value = subprocess.CompletedProcess([], 0)
            req.return_value = SimpleNamespace(ok=True, json=fake_json)
            _ensure_release()

        release.refresh_from_db()
        self.assertTrue(release.is_certified)
        self.assertTrue(release.is_published)

import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase

from core.models import Package, PackageRelease
from nodes.apps import _ensure_release
from utils import revision


class EnsureReleaseTests(TestCase):
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

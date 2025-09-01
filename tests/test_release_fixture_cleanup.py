import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase

from core.models import Package, PackageRelease


class ReleaseFixtureCleanupTests(TestCase):
    def setUp(self):
        self.package, _ = Package.objects.get_or_create(name="pkg")
        self.release = PackageRelease.objects.create(
            package=self.package,
            version="1.0.0",
            pypi_url="https://pypi.org/project/pkg/1.0.0/",
        )
        PackageRelease.dump_fixture()
        self.fixture_path = Path("core/fixtures/releases.json")

    def test_delete_removes_from_fixture(self):
        self.assertIn("1.0.0", self.fixture_path.read_text())
        self.release.delete()
        data = self.fixture_path.read_text()
        self.assertNotIn("1.0.0", data)


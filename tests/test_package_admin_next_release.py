from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase

from core.admin import PackageAdmin
from core.models import Package, PackageRelease


class PackageAdminPrepareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = PackageAdmin(Package, AdminSite())
        self.package = Package.objects.create(name="pkg")
        self.version_file = Path("VERSION")
        self.original_version = self.version_file.read_text()
        self.addCleanup(lambda: self.version_file.write_text(self.original_version))

    def _mock_pypi(self, version: str):
        resp = MagicMock(ok=True)
        resp.json.return_value = {"releases": {version: []}}
        return resp

    @patch("core.admin.requests.get")
    def test_repo_version_preferred_over_pypi(self, mock_get):
        mock_get.return_value = self._mock_pypi("0.1.8")
        self.version_file.write_text("0.1.9")
        self.admin._prepare(self.factory.get("/"), self.package)
        release = PackageRelease.objects.get(package=self.package)
        self.assertEqual(release.version, "0.1.9")

    @patch("core.admin.requests.get")
    def test_pypi_plus_one_used_when_greater(self, mock_get):
        mock_get.return_value = self._mock_pypi("0.1.8")
        self.version_file.write_text("0.1.7")
        self.admin._prepare(self.factory.get("/"), self.package)
        release = PackageRelease.objects.get(package=self.package)
        self.assertEqual(release.version, "0.1.9")

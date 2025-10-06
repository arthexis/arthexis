import os
from types import SimpleNamespace
from unittest import mock

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402


django.setup()  # noqa: E402

from django.test import TestCase  # noqa: E402

from core.models import Package, PackageRelease, ReleaseManager, User  # noqa: E402
from core import release as release_utils  # noqa: E402


class PyPICheckReadinessTests(TestCase):
    def setUp(self) -> None:
        Package.objects.all().delete()
        User.objects.filter(username="manager").delete()
        self.user = User.objects.create_superuser(
            "manager", "manager@example.com", "pw"
        )
        self.manager = ReleaseManager.objects.create(
            user=self.user,
            pypi_token="token-value",
        )
        self.package = Package.objects.create(
            name="pkg-check",
            release_manager=self.manager,
            is_active=True,
        )
        self.release = PackageRelease.objects.create(
            package=self.package,
            release_manager=self.manager,
            version="1.2.3",
        )

    @mock.patch("core.release.requests.get")
    @mock.patch("core.release.subprocess.run")
    @mock.patch("core.release.network_available", return_value=True)
    def test_successful_check(self, network_available, run, get):
        run.return_value = SimpleNamespace(stdout="twine version 6.1.0", stderr="")
        api_response = SimpleNamespace(ok=True, status_code=200)
        api_response.json = lambda: {"releases": {}}
        upload_response = SimpleNamespace(ok=True, status_code=200)
        get.side_effect = [api_response, upload_response]

        result = release_utils.check_pypi_readiness(release=self.release)

        self.assertTrue(result.ok)
        levels = {message for level, message in result.messages if level == "success"}
        self.assertTrue(any("Twine" in message for message in levels))
        self.assertTrue(
            any("PyPI JSON API reachable" in message for message in levels)
        )
        self.assertEqual(get.call_count, 2)

    @mock.patch("core.release.requests.get")
    @mock.patch("core.release.subprocess.run")
    @mock.patch("core.release.network_available", return_value=True)
    def test_missing_credentials_reports_error(self, network_available, run, get):
        self.manager.pypi_token = ""
        self.manager.save(update_fields=["pypi_token"])
        run.return_value = SimpleNamespace(stdout="twine version 6.1.0", stderr="")
        api_response = SimpleNamespace(ok=True, status_code=200)
        api_response.json = lambda: {"releases": {}}
        upload_response = SimpleNamespace(ok=True, status_code=200)
        get.side_effect = [api_response, upload_response]

        result = release_utils.check_pypi_readiness(release=self.release)

        self.assertFalse(result.ok)
        self.assertTrue(
            any(level == "error" and "Missing PyPI credentials" in message for level, message in result.messages)
        )

    @mock.patch("core.release.requests.get")
    @mock.patch("core.release.subprocess.run")
    @mock.patch("core.release.network_available", return_value=True)
    def test_environment_credentials_used_when_available(
        self, network_available, run, get
    ):
        self.manager.pypi_token = ""
        self.manager.save(update_fields=["pypi_token"])
        run.return_value = SimpleNamespace(stdout="twine version 6.1.0", stderr="")
        api_response = SimpleNamespace(ok=True, status_code=200)
        api_response.json = lambda: {"releases": {}}
        upload_response = SimpleNamespace(ok=True, status_code=200)
        get.side_effect = [api_response, upload_response]

        with mock.patch.dict(os.environ, {"PYPI_API_TOKEN": "env-token"}, clear=False):
            result = release_utils.check_pypi_readiness(release=self.release)

        self.assertTrue(result.ok)
        self.assertTrue(
            any(
                level == "success" and "environment variables" in message
                for level, message in result.messages
            )
        )

    @mock.patch("core.release.subprocess.run")
    @mock.patch("core.release.network_available", return_value=False)
    def test_offline_mode_skips_network_checks(self, network_available, run):
        run.return_value = SimpleNamespace(stdout="twine version 6.1.0", stderr="")

        result = release_utils.check_pypi_readiness(release=self.release)

        self.assertTrue(result.ok)
        self.assertTrue(
            any(
                level == "warning" and "Offline mode" in message
                for level, message in result.messages
            )
        )


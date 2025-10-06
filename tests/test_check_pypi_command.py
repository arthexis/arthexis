import os
from types import SimpleNamespace
from unittest import mock

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402


django.setup()  # noqa: E402

from django.core.management import call_command, CommandError  # noqa: E402
from django.test import TestCase  # noqa: E402

from core.models import Package, PackageRelease, ReleaseManager, User  # noqa: E402


class CheckPyPICommandTests(TestCase):
    def setUp(self) -> None:
        Package.objects.all().delete()
        self.user = User.objects.create_superuser("manager", "manager@example.com", "pw")
        self.manager = ReleaseManager.objects.create(user=self.user, pypi_token="token")
        self.package = Package.objects.create(
            name="pkg-cmd",
            release_manager=self.manager,
            is_active=True,
        )
        self.release = PackageRelease.objects.create(
            package=self.package,
            release_manager=self.manager,
            version="1.0.1",
        )

    def _result(self, ok=True, messages=None):
        if messages is None:
            messages = [("success", "All good")]
        return SimpleNamespace(ok=ok, messages=messages)

    @mock.patch("core.management.commands.check_pypi.release_utils.check_pypi_readiness")
    def test_command_uses_release_identifier(self, check):
        check.return_value = self._result()
        out = mock.Mock()
        err = mock.Mock()

        call_command("check_pypi", str(self.release.pk), stdout=out, stderr=err)

        check.assert_called_once_with(release=self.release)
        out.write.assert_any_call(mock.ANY)

    @mock.patch("core.management.commands.check_pypi.release_utils.check_pypi_readiness")
    def test_command_accepts_version(self, check):
        check.return_value = self._result()
        out = mock.Mock()
        err = mock.Mock()

        call_command("check_pypi", self.release.version, stdout=out, stderr=err)

        check.assert_called_once_with(release=self.release)

    @mock.patch("core.management.commands.check_pypi.release_utils.check_pypi_readiness")
    def test_command_without_identifier_uses_active_release(self, check):
        check.return_value = self._result()
        out = mock.Mock()
        err = mock.Mock()

        call_command("check_pypi", stdout=out, stderr=err)

        check.assert_called_once_with(release=self.release)

    @mock.patch("core.management.commands.check_pypi.release_utils.check_pypi_readiness")
    def test_command_reports_errors_in_exit_code(self, check):
        check.return_value = self._result(ok=False, messages=[("error", "Missing token")])
        out = mock.Mock()
        err = mock.Mock()

        with self.assertRaises(CommandError):
            call_command("check_pypi", str(self.release.pk), stdout=out, stderr=err)

        err.write.assert_any_call(mock.ANY)

    def test_missing_release_raises_error(self):
        with self.assertRaises(CommandError):
            call_command("check_pypi", "unknown")


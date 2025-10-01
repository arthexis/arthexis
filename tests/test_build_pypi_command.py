import os
import sys
from pathlib import Path
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402


django.setup()  # noqa: E402

from django.core.management import CommandError, call_command  # noqa: E402
from django.test import TestCase  # noqa: E402

from core.models import Package  # noqa: E402


class BuildPyPICommandTests(TestCase):
    def setUp(self) -> None:
        Package.objects.all().delete()

    def _create_package(self, **overrides):
        defaults = {
            "name": "custom-package",
            "description": "Custom package",
            "author": "Example Author",
            "email": "author@example.com",
            "python_requires": ">=3.11",
            "license": "MIT",
            "repository_url": "https://example.com/repo",
            "homepage_url": "https://example.com",
        }
        defaults.update(overrides)
        return Package.objects.create(**defaults)

    def test_package_selected_by_id(self) -> None:
        package = self._create_package(name="package-by-id")

        with mock.patch("core.management.commands.build_pypi.release.build") as build_mock:
            call_command("build_pypi", "--package", str(package.pk))

        build_mock.assert_called_once()
        kwargs = build_mock.call_args.kwargs
        self.assertEqual(kwargs["package"].name, package.name)

    def test_package_selected_by_name(self) -> None:
        package = self._create_package(name="package-by-name")

        with mock.patch("core.management.commands.build_pypi.release.build") as build_mock:
            call_command("build_pypi", "--package", package.name)

        build_mock.assert_called_once()
        kwargs = build_mock.call_args.kwargs
        self.assertEqual(kwargs["package"].name, package.name)

    def test_missing_configuration_raises_error(self) -> None:
        package = self._create_package(
            name="incomplete-package",
            description="",
            repository_url="",
        )

        with mock.patch("core.management.commands.build_pypi.release.build") as build_mock:
            with self.assertRaisesMessage(
                CommandError,
                "missing required packaging configuration: description, repository url.",
            ):
                call_command("build_pypi", "--package", package.name)

        build_mock.assert_not_called()

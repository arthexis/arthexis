import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Package, PackageRelease, ReleaseManager


class PackageReleaseTargetsTests(TestCase):
    def setUp(self):
        self.User = get_user_model()

    def _create_user(self, username: str) -> object:
        return self.User.objects.create_user(
            username,
            email=f"{username}@example.com",
            password="password",
        )

    def test_primary_and_secondary_targets_use_package_configuration(self):
        package = Package.objects.create(
            name="pkg-primary",
            description="Test package",
            author="Author",
            email="author@example.com",
            python_requires=">=3.10",
            license="GPL",
            pypi_repository_url="https://upload.example.com/primary/",
            pypi_token="pkg-token",
            secondary_pypi_url="https://upload.example.com/secondary/",
            secondary_pypi_username="pkg-user",
            secondary_pypi_password="pkg-pass",
        )
        release = PackageRelease.objects.create(package=package, version="1.2.3")

        with mock.patch.dict(
            os.environ,
            {
                "PYPI_REPOSITORY_URL": "",
                "PYPI_API_TOKEN": "",
                "PYPI_USERNAME": "",
                "PYPI_PASSWORD": "",
                "PYPI_SECONDARY_URL": "",
                "PYPI_SECONDARY_USERNAME": "",
                "PYPI_SECONDARY_PASSWORD": "",
                "GITHUB_USERNAME": "",
                "GITHUB_ACTOR": "",
            },
            clear=False,
        ):
            targets = release.build_publish_targets()

        self.assertEqual(len(targets), 2)
        primary, secondary = targets
        self.assertEqual(primary.repository_url, "https://upload.example.com/primary/")
        self.assertIsNotNone(primary.credentials)
        self.assertEqual(primary.credentials.token, "pkg-token")
        self.assertIsNone(primary.credentials.username)
        self.assertIsNone(primary.credentials.password)

        self.assertEqual(secondary.repository_url, "https://upload.example.com/secondary/")
        self.assertIsNotNone(secondary.credentials)
        self.assertEqual(secondary.credentials.username, "pkg-user")
        self.assertEqual(secondary.credentials.password, "pkg-pass")

    def test_primary_target_falls_back_to_package_release_manager(self):
        pkg_manager_user = self._create_user("pkg-manager")
        pkg_manager = ReleaseManager.objects.create(
            user=pkg_manager_user,
            pypi_url="https://pkg-manager.example.com/",
            pypi_token="pkg-token",
        )

        package = Package.objects.create(
            name="pkg-fallback",
            description="Fallback package",
            author="Author",
            email="author@example.com",
            python_requires=">=3.10",
            license="GPL",
            release_manager=pkg_manager,
        )

        release_manager_user = self._create_user("release-manager")
        release_manager = ReleaseManager.objects.create(user=release_manager_user)

        release = PackageRelease.objects.create(
            package=package,
            version="2.0.0",
            release_manager=release_manager,
        )

        with mock.patch.dict(
            os.environ,
            {
                "PYPI_REPOSITORY_URL": "",
                "PYPI_API_TOKEN": "",
                "PYPI_USERNAME": "",
                "PYPI_PASSWORD": "",
            },
            clear=False,
        ):
            targets = release.build_publish_targets()

        self.assertEqual(targets[0].repository_url, "https://pkg-manager.example.com/")
        self.assertIsNotNone(targets[0].credentials)
        self.assertEqual(targets[0].credentials.token, "pkg-token")

    def test_primary_target_uses_environment_when_unconfigured(self):
        package = Package.objects.create(
            name="pkg-env",
            description="Env package",
            author="Author",
            email="author@example.com",
            python_requires=">=3.10",
            license="GPL",
        )
        release = PackageRelease.objects.create(package=package, version="3.0.0")

        env = {
            "PYPI_REPOSITORY_URL": "https://upload.example.com/env/",
            "PYPI_API_TOKEN": "env-token",
            "PYPI_USERNAME": "env-user",
            "PYPI_PASSWORD": "env-pass",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            targets = release.build_publish_targets()

        primary = targets[0]
        self.assertEqual(primary.repository_url, env["PYPI_REPOSITORY_URL"])
        self.assertIsNotNone(primary.credentials)
        # Environment token takes precedence over username/password when present
        self.assertEqual(primary.credentials.token, "env-token")

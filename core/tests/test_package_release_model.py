from __future__ import annotations

import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Package, PackageRelease, ReleaseManager


class PackageReleaseCredentialTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_model = get_user_model()
        self.user_counter = 0

    def create_manager(self, **kwargs) -> ReleaseManager:
        self.user_counter += 1
        user = self.user_model.objects.create_user(
            username=f"manager{self.user_counter}", password="pass"
        )
        return ReleaseManager.objects.create(user=user, **kwargs)

    def test_credential_and_token_hierarchy(self) -> None:
        release_manager = self.create_manager(
            pypi_token="release-pypi-token", github_token="release-github-token"
        )
        package = Package.objects.create(name="test-package")
        release = PackageRelease.objects.create(
            package=package, release_manager=release_manager, version="1.0.0"
        )

        release_creds = release.to_credentials()
        self.assertIsNotNone(release_creds)
        self.assertEqual(release_creds.token, "release-pypi-token")
        self.assertEqual(release.get_github_token(), "release-github-token")

        package_manager = self.create_manager(
            pypi_token="package-pypi-token", github_token="package-github-token"
        )
        release.release_manager = None
        release.save(update_fields=["release_manager"])
        package.release_manager = package_manager
        package.save(update_fields=["release_manager"])
        release.refresh_from_db()

        package_creds = release.to_credentials()
        self.assertIsNotNone(package_creds)
        self.assertEqual(package_creds.token, "package-pypi-token")
        self.assertEqual(release.get_github_token(), "package-github-token")

        package.release_manager = None
        package.save(update_fields=["release_manager"])
        release.refresh_from_db()

        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "env-token"}, clear=True):
            self.assertIsNone(release.to_credentials())
            self.assertEqual(release.get_github_token(), "env-token")


import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Package, PackageRelease, ReleaseManager


class GitHubTokenTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.get(username="arthexis")
        self.manager = ReleaseManager.objects.get(user=self.user)
        package, _ = Package.objects.get_or_create(name="arthexis")
        self.release, _ = PackageRelease.objects.get_or_create(
            package=package, version="0.1.1"
        )

    def test_profile_token_preferred_over_env(self):
        self.manager.github_token = "profile-token"
        self.manager.save()
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "env-token"}, clear=False):
            self.assertEqual(self.release.get_github_token(), "profile-token")

    def test_env_token_used_when_profile_missing(self):
        self.manager.github_token = ""
        self.manager.save()
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "env-token"}, clear=False):
            self.assertEqual(self.release.get_github_token(), "env-token")

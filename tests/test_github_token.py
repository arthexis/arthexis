import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import PackageRelease, PackagerProfile


class GitHubTokenTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.get(username="arthexis")
        self.profile = PackagerProfile.objects.get(user=self.user)
        self.release = PackageRelease.objects.get(version="0.1.1")

    def test_profile_token_preferred_over_env(self):
        self.profile.github_token = "profile-token"
        self.profile.save()
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "env-token"}, clear=False):
            self.assertEqual(self.release.get_github_token(), "profile-token")

    def test_env_token_used_when_profile_missing(self):
        self.profile.github_token = ""
        self.profile.save()
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "env-token"}, clear=False):
            self.assertEqual(self.release.get_github_token(), "env-token")

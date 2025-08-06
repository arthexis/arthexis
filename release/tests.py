from django.test import SimpleTestCase, TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from unittest.mock import patch

from . import Credentials, DEFAULT_PACKAGE
from .models import PackageConfig


class CredentialsTests(SimpleTestCase):
    def test_token_args(self):
        c = Credentials(token="abc")
        self.assertEqual(c.twine_args(), ["--username", "__token__", "--password", "abc"])

    def test_userpass_args(self):
        c = Credentials(username="u", password="p")
        self.assertEqual(c.twine_args(), ["--username", "u", "--password", "p"])

    def test_missing(self):
        c = Credentials()
        with self.assertRaises(ValueError):
            c.twine_args()


class PackageTests(SimpleTestCase):
    def test_default_name(self):
        self.assertEqual(DEFAULT_PACKAGE.name, "arthexis")


class PackageConfigTests(TestCase):
    def test_to_package_and_credentials(self):
        cfg = PackageConfig.objects.create(
            name="pkg",
            description="desc",
            author="me",
            email="me@example.com",
            python_requires=">=3.10",
            license="MIT",
        )
        package = cfg.to_package()
        self.assertEqual(package.name, "pkg")
        self.assertIsNone(cfg.to_credentials())


class PackageAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="admin", password="pass", email="a@a.com"
        )
        self.client = Client()
        self.client.force_login(self.admin)
        self.cfg = PackageConfig.objects.create(
            name="pkg",
            description="desc",
            author="me",
            email="me@example.com",
            python_requires=">=3.10",
            license="MIT",
            token="tok",
        )

    def test_build_action_calls_utils(self):
        url = reverse("admin:release_packageconfig_changelist")
        response = self.client.post(
            url, {"action": "build_package", "_selected_action": [self.cfg.pk]}
        )
        self.assertEqual(response.status_code, 200)
        with patch("release.admin.utils.build") as mock_build:
            response = self.client.post(
                url,
                {
                    "action": "build_package",
                    "_selected_action": [self.cfg.pk],
                    "apply": "1",
                },
                follow=True,
            )
            self.assertEqual(response.status_code, 200)
            mock_build.assert_called_once()

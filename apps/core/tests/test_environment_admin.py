from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.core.environment import _legacy_user_env_path, _user_env_path


class EnvironmentAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="arthexis",
            email="arthexis@example.com",
            password="testpass123",
        )
        self.client.force_login(self.user)

        self.tmpdir = TemporaryDirectory()
        self.base_dir = Path(self.tmpdir.name)
        self.base_dir_patch = patch("apps.core.environment.settings.BASE_DIR", self.base_dir)
        self.base_dir_patch.start()
        self.addCleanup(self.base_dir_patch.stop)
        self.addCleanup(self.tmpdir.cleanup)

    def test_environment_page_shows_personal_env_filename(self):
        response = self.client.get(reverse("admin:environment"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "arthexis-1.env")
        self.assertContains(response, "var/user_env/arthexis-1.env")

    def test_download_returns_personal_env_file_content(self):
        env_path = _user_env_path(self.user)
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("ALPHA=1\nBETA=two\n", encoding="utf-8")

        response = self.client.get(reverse("admin:environment-download"))

        self.assertEqual(response.status_code, 200)
        self.assertIn('filename="arthexis-1.env"', response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"ALPHA=1\nBETA=two\n")

    def test_download_falls_back_to_legacy_env_file_content(self):
        legacy_path = _legacy_user_env_path(self.user)
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text("LEGACY=yes\n", encoding="utf-8")

        response = self.client.get(reverse("admin:environment-download"))

        self.assertEqual(response.status_code, 200)
        self.assertIn('filename="arthexis-1.env"', response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"LEGACY=yes\n")

    def test_upload_replaces_personal_env_values(self):
        upload = SimpleUploadedFile(
            "personal.env",
            b"FOO=bar\nSPACED = value\nINVALID\n",
            content_type="text/plain",
        )

        response = self.client.post(
            reverse("admin:environment"),
            {"personal_env_upload": upload},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Personal .env file uploaded")
        env_path = _user_env_path(self.user)
        self.assertTrue(env_path.exists())
        self.assertEqual(env_path.read_text(encoding="utf-8"), "FOO=bar\nSPACED=value\n")

    def test_user_env_filename_is_collision_safe(self):
        other_user = get_user_model().objects.create_superuser(
            username="arthexis+1",
            email="other@example.com",
            password="testpass123",
        )

        self.assertNotEqual(_user_env_path(self.user), _user_env_path(other_user))

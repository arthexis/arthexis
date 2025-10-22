import os
import sys
import tempfile
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings


class ReadmeEditorTests(TestCase):
    def setUp(self):
        super().setUp()
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.base_path = Path(self._tempdir.name)
        self.override = override_settings(BASE_DIR=str(self.base_path))
        self.override.enable()
        self.addCleanup(self.override.disable)
        (self.base_path / "README.md").write_text("# Hello\n", encoding="utf-8")
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            "admin", "admin@example.com", "password123"
        )
        self.user = User.objects.create_user(
            "user", "user@example.com", "password123"
        )

    def test_superuser_sees_edit_button(self):
        self.client.force_login(self.superuser)
        response = self.client.get("/read/")
        self.assertContains(response, "Edit document")
        self.assertContains(response, "/read/README.md/edit/")

    def test_non_superuser_does_not_see_edit_button(self):
        self.client.force_login(self.user)
        response = self.client.get("/read/")
        self.assertNotContains(response, "Edit document")

    def test_edit_view_requires_superuser(self):
        self.client.force_login(self.user)
        response = self.client.get("/read/README.md/edit/")
        self.assertEqual(response.status_code, 403)

    def test_edit_view_updates_document(self):
        self.client.force_login(self.superuser)
        response = self.client.post(
            "/read/README.md/edit/",
            {"content": "# Updated\n\nContent"},
        )
        self.assertRedirects(response, "/read/README.md/edit/")
        updated = (self.base_path / "README.md").read_text(encoding="utf-8")
        self.assertEqual(updated, "# Updated\n\nContent")

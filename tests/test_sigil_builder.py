from io import BytesIO

from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import TestCase

from core.sigil_builder import resolve_sigils_in_text


class SigilBuilderTests(TestCase):
    def setUp(self):
        User = get_user_model()
        User.all_objects.filter(username="admin").delete()
        self.user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="admin"
        )
        self.client.force_login(self.user)

    def test_resolve_multiple_sigils_in_text(self):
        text = "Lang: [SYS.LANGUAGE-CODE], Debug: [SYS.DEBUG]"
        resolved = resolve_sigils_in_text(text)
        expected = f"Lang: {settings.LANGUAGE_CODE}, Debug: {settings.DEBUG}"
        self.assertEqual(resolved, expected)

    def test_file_upload_resolves_sigils(self):
        content = "[SYS.LANGUAGE-CODE]"
        upload = BytesIO(content.encode("utf-8"))
        upload.name = "sigils.txt"
        response = self.client.post(
            "/admin/sigil-builder/",
            {"sigils_file": upload},
        )
        self.assertContains(response, settings.LANGUAGE_CODE)

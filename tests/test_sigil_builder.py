from io import BytesIO

from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import TestCase

from core.sigil_builder import generate_model_sigils, resolve_sigils_in_text


class SigilBuilderTests(TestCase):
    def setUp(self):
        User = get_user_model()
        User.all_objects.filter(username="admin").delete()
        self.user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="admin"
        )
        self.client.force_login(self.user)

    def test_resolve_multiple_sigils_in_text(self):
        text = "Lang: [CONF.LANGUAGE-CODE], Debug: [CONF.DEBUG]"
        resolved = resolve_sigils_in_text(text)
        expected = f"Lang: {settings.LANGUAGE_CODE}, Debug: {settings.DEBUG}"
        self.assertEqual(resolved, expected)

    def test_file_upload_resolves_sigils(self):
        content = "[CONF.LANGUAGE-CODE]"
        upload = BytesIO(content.encode("utf-8"))
        upload.name = "sigils.txt"
        response = self.client.post(
            "/admin/sigil-builder/",
            {"sigils_file": upload},
        )
        self.assertContains(response, settings.LANGUAGE_CODE)
        self.assertFalse(response.context["show_sigils_input"])
        self.assertTrue(response.context["show_result"])

    def test_builder_groups_multiple_roots(self):
        from django.contrib.contenttypes.models import ContentType
        from teams.models import EmailInbox
        from core.models import SigilRoot

        ct = ContentType.objects.get_for_model(EmailInbox)
        SigilRoot.objects.get_or_create(
            prefix="INBOX",
            context_type=SigilRoot.Context.ENTITY,
            content_type=ct,
        )
        SigilRoot.objects.get_or_create(
            prefix="EMAIL",
            context_type=SigilRoot.Context.ENTITY,
            content_type=ct,
        )
        response = self.client.get("/admin/sigil-builder/")
        self.assertContains(response, "[INBOX]")
        self.assertContains(response, "[EMAIL]")
        content = response.content.decode()
        self.assertEqual(content.count("Email Inbox"), 1)

    def test_auto_fields_include_roots(self):
        from django.contrib.contenttypes.models import ContentType
        from teams.models import EmailInbox
        from core.models import SigilRoot

        ct = ContentType.objects.get_for_model(EmailInbox)
        SigilRoot.objects.get_or_create(
            prefix="INBOX",
            context_type=SigilRoot.Context.ENTITY,
            content_type=ct,
        )
        response = self.client.get("/admin/sigil-builder/")
        content = response.content.decode()
        self.assertIn("<th>Root</th>", content)
        self.assertGreater(content.count("[INBOX]"), 1)

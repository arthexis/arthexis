from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from core.sigil_builder import _sigil_builder_view


class SigilBuilderViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="password123"
        )
        self.client = Client()
        self.factory = RequestFactory()

    def _add_session(self, request):
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()

    def test_empty_file_upload_returns_error(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("admin:sigil_builder"),
            {
                "sigils_file": SimpleUploadedFile(
                    "sigils.txt", b"", content_type="text/plain"
                )
            },
        )

        self.assertContains(response, "Uploaded file is empty.")
        self.assertIn("errors", response.context)
        self.assertTrue(response.context["errors"])
        self.assertFalse(response.context["show_result"])
        self.assertEqual(response.context["resolved_text"], "")

    def test_unreadable_file_upload_returns_error(self):
        class UnreadableFile:
            name = "sigils.txt"
            size = 1

        request = self.factory.post(reverse("admin:sigil_builder"), {})
        request.user = self.user
        self._add_session(request)
        request.FILES["sigils_file"] = UnreadableFile()

        response = _sigil_builder_view(request)

        self.assertIn("Uploaded file could not be processed.", response.rendered_content)
        self.assertIn("errors", response.context_data)
        self.assertTrue(response.context_data["errors"])
        self.assertFalse(response.context_data["show_result"])

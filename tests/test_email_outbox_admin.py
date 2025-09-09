from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory

from nodes.admin import EmailOutboxAdmin, EmailOutbox as AdminEmailOutbox
from nodes.models import EmailOutbox, Node


class EmailOutboxAdminActionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="admin", email="a@example.com", password="pwd"
        )
        self.node = Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            port=8000,
            mac_address="00:11:22:33:44:55",
        )
        self.outbox = EmailOutbox.objects.create(
            node=self.node,
            host="smtp.test",
            port=25,
            username="u",
            password="p",
        )
        self.factory = RequestFactory()
        self.admin = EmailOutboxAdmin(AdminEmailOutbox, AdminSite())

    def test_test_outbox_action(self):
        request = self.factory.get("/")
        request.user = self.user
        request.session = self.client.session
        from django.contrib.messages.storage.fallback import FallbackStorage

        request._messages = FallbackStorage(request)
        with patch.object(EmailOutbox, "send_mail") as mock_send:
            response = self.admin.test_outbox(request, str(self.outbox.pk))
            self.assertEqual(response.status_code, 302)
            mock_send.assert_called_once()

    def test_change_form_contains_link(self):
        request = self.factory.get("/")
        request.user = self.user
        response = self.admin.changeform_view(request, str(self.outbox.pk))
        content = response.render().content.decode()
        self.assertIn("Test Outbox", content)

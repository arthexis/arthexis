from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.admin.sites import AdminSite
from unittest.mock import patch

from core.models import EmailInbox
from core.admin import (
    EmailInboxAdminForm,
    EmailInboxAdmin,
    EmailInbox as AdminEmailInbox,
)


class EmailInboxAdminFormTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="mail", password="pwd")

    def _create_inbox(self, password="secret"):
        return EmailInbox.objects.create(
            user=self.user,
            host="mail.test",
            port=993,
            username="mail",
            password=password,
            protocol=EmailInbox.IMAP,
            use_ssl=True,
        )

    def test_password_field_hidden_and_blank_initial(self):
        inbox = self._create_inbox()
        form = EmailInboxAdminForm(instance=inbox)
        html = form.as_p()
        self.assertIn('type="password"', html)
        self.assertNotIn("secret", html)

    def test_blank_password_keeps_existing(self):
        inbox = self._create_inbox()
        data = {
            "user": self.user.pk,
            "host": "mail2.test",
            "port": 993,
            "username": "mail",
            "password": "",
            "protocol": EmailInbox.IMAP,
            "use_ssl": True,
        }
        form = EmailInboxAdminForm(data, instance=inbox)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        inbox.refresh_from_db()
        self.assertEqual(inbox.password, "secret")
        self.assertEqual(inbox.host, "mail2.test")

    def test_new_password_saved(self):
        inbox = self._create_inbox()
        data = {
            "user": self.user.pk,
            "host": "mail.test",
            "port": 993,
            "username": "mail",
            "password": "newpass",
            "protocol": EmailInbox.IMAP,
            "use_ssl": True,
        }
        form = EmailInboxAdminForm(data, instance=inbox)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        inbox.refresh_from_db()
        self.assertEqual(inbox.password, "newpass")


class EmailInboxAdminActionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="admin", email="a@example.com", password="pwd"
        )
        self.inbox = EmailInbox.objects.create(
            user=self.user,
            host="imap.test",
            port=993,
            username="u",
            password="p",
            protocol=EmailInbox.IMAP,
            use_ssl=True,
        )
        self.factory = RequestFactory()
        self.admin = EmailInboxAdmin(AdminEmailInbox, AdminSite())

    def test_test_inbox_action(self):
        request = self.factory.get("/")
        request.user = self.user
        request.session = self.client.session
        from django.contrib.messages.storage.fallback import FallbackStorage

        request._messages = FallbackStorage(request)
        with patch.object(EmailInbox, "test_connection") as mock_test:
            response = self.admin.test_inbox(request, str(self.inbox.pk))
            self.assertEqual(response.status_code, 302)
            mock_test.assert_called_once()

    def test_change_form_contains_link(self):
        request = self.factory.get("/")
        request.user = self.user
        response = self.admin.changeform_view(request, str(self.inbox.pk))
        content = response.render().content.decode()
        self.assertIn("Test Inbox", content)

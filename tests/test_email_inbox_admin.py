from django.test import TestCase
from django.contrib.auth import get_user_model

from core.models import EmailInbox
from core.admin import EmailInboxAdminForm


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

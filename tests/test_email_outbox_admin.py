from unittest.mock import patch

import os

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory

from nodes.admin import EmailOutboxAdmin, EmailOutbox as AdminEmailOutbox
from teams.models import EmailOutbox
from core.admin import EmailOutboxAdminForm, KeepExistingValue


class EmailOutboxAdminActionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="admin", email="a@example.com", password="pwd"
        )
        self.outbox = EmailOutbox.objects.create(
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


class EmailOutboxAdminFormTests(TestCase):
    def setUp(self):
        self.outbox = EmailOutbox.objects.create(
            host="smtp.test",
            port=587,
            username="user",
            password="secret",
            use_tls=True,
            use_ssl=False,
            is_enabled=True,
        )

    def _base_form_data(self, **overrides):
        data = {
            "host": "smtp.test",
            "port": 587,
            "username": "user",
            "password": "",
            "use_tls": True,
            "use_ssl": False,
            "from_email": "",
            "is_enabled": True,
        }
        data.update(overrides)
        return data

    def test_password_field_hidden_and_blank_initial(self):
        form = EmailOutboxAdminForm(instance=self.outbox)
        html = form.as_p()
        self.assertIn('type="password"', html)
        self.assertNotIn("secret", html)

    def test_blank_password_keeps_existing(self):
        data = self._base_form_data()
        form = EmailOutboxAdminForm(data, instance=self.outbox)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.outbox.refresh_from_db()
        self.assertEqual(self.outbox.password, "secret")

    def test_new_password_saved(self):
        data = self._base_form_data(password="newpass")
        form = EmailOutboxAdminForm(data, instance=self.outbox)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.outbox.refresh_from_db()
        self.assertEqual(self.outbox.password, "newpass")

    def test_blank_password_preserves_sigil_placeholder(self):
        self.addCleanup(lambda: os.environ.pop("SMTP_OUTBOX_PASSWORD", None))
        os.environ.pop("SMTP_OUTBOX_PASSWORD", None)
        self.outbox.password = "[ENV.SMTP_OUTBOX_PASSWORD]"
        self.outbox.save(update_fields=["password"])
        original = (
            EmailOutbox.objects.filter(pk=self.outbox.pk)
            .values_list("password", flat=True)
            .get()
        )
        self.assertEqual(original, "[ENV.SMTP_OUTBOX_PASSWORD]")
        os.environ["SMTP_OUTBOX_PASSWORD"] = "env-secret"
        data = self._base_form_data()
        form = EmailOutboxAdminForm(data, instance=self.outbox)
        self.assertTrue(form.is_valid(), form.errors)
        value = form.cleaned_data["password"]
        self.assertIsInstance(value, KeepExistingValue)
        self.assertEqual(value.field, "password")
        saved = form.save()
        field = saved._meta.get_field("password")
        self.assertEqual(field.value_from_object(saved), "[ENV.SMTP_OUTBOX_PASSWORD]")
        stored = (
            EmailOutbox.objects.filter(pk=saved.pk)
            .values_list("password", flat=True)
            .get()
        )
        self.assertEqual(stored, "[ENV.SMTP_OUTBOX_PASSWORD]")
        self.assertEqual(saved.password, "env-secret")

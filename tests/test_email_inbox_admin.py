from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.admin.sites import AdminSite
import os

from django.contrib import admin
from unittest.mock import patch

from teams.models import EmailInbox, EmailCollector
from core.admin import (
    EmailInboxAdminForm,
    EmailInboxAdmin,
    EmailCollectorAdmin,
    KeepExistingValue,
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

    def test_blank_password_preserves_sigil_placeholder(self):
        self.addCleanup(lambda: os.environ.pop("SMTP_PASSWORD", None))
        os.environ.pop("SMTP_PASSWORD", None)
        inbox = self._create_inbox(password="[ENV.SMTP_PASSWORD]")
        original = (
            EmailInbox.objects.filter(pk=inbox.pk)
            .values_list("password", flat=True)
            .get()
        )
        self.assertEqual(original, "[ENV.SMTP_PASSWORD]")
        os.environ["SMTP_PASSWORD"] = "env-secret"
        data = {
            "user": self.user.pk,
            "host": "mail.test",
            "port": 993,
            "username": "mail",
            "password": "",
            "protocol": EmailInbox.IMAP,
            "use_ssl": True,
        }
        form = EmailInboxAdminForm(data, instance=inbox)
        self.assertTrue(form.is_valid(), form.errors)
        value = form.cleaned_data["password"]
        self.assertIsInstance(value, KeepExistingValue)
        self.assertEqual(value.field, "password")
        self.assertFalse(value)
        field = form.instance._meta.get_field("password")
        self.assertEqual(field.value_from_object(form.instance), "[ENV.SMTP_PASSWORD]")
        saved = form.save()
        field = saved._meta.get_field("password")
        self.assertEqual(field.value_from_object(saved), "[ENV.SMTP_PASSWORD]")
        stored = (
            EmailInbox.objects.filter(pk=saved.pk)
            .values_list("password", flat=True)
            .get()
        )
        self.assertEqual(stored, "[ENV.SMTP_PASSWORD]")
        self.assertEqual(saved.password, "env-secret")


class EmailInboxAdminActionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="manager", email="a@example.com", password="pwd"
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
        self.admin = EmailInboxAdmin(EmailInbox, AdminSite())

    def test_my_profile_redirects_to_existing_profile(self):
        request = self.factory.get("/")
        request.user = self.user
        request.session = self.client.session
        from django.contrib.messages.storage.fallback import FallbackStorage

        request._messages = FallbackStorage(request)
        response = self.admin.my_profile(request, EmailInbox.objects.none())
        self.assertEqual(response.status_code, 302)
        opts = self.admin.model._meta
        expected = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_change", args=[self.inbox.pk]
        )
        self.assertEqual(response.url, expected)

    def test_my_profile_redirects_to_add_when_missing(self):
        self.inbox.delete()
        request = self.factory.get("/")
        request.user = self.user
        request.session = self.client.session
        from django.contrib.messages.storage.fallback import FallbackStorage

        request._messages = FallbackStorage(request)
        response = self.admin.my_profile(request, EmailInbox.objects.none())
        self.assertEqual(response.status_code, 302)
        opts = self.admin.model._meta
        expected = (
            f"{reverse(f'admin:{opts.app_label}_{opts.model_name}_add')}?user={self.user.pk}"
        )
        self.assertEqual(response.url, expected)

    def test_my_profile_without_add_permission_shows_error(self):
        self.inbox.delete()
        User = get_user_model()
        limited = User.objects.create_user(
            username="limited", password="pwd", is_staff=True
        )
        request = self.factory.get("/")
        request.user = limited
        request.session = self.client.session
        from django.contrib.messages.storage.fallback import FallbackStorage

        request._messages = FallbackStorage(request)
        response = self.admin.my_profile(request, EmailInbox.objects.none())
        self.assertEqual(response.status_code, 302)
        opts = self.admin.model._meta
        self.assertEqual(
            response.url,
            reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist"),
        )
        messages = [m.message.lower() for m in request._messages]
        self.assertTrue(any("permission" in message for message in messages))

    def test_my_profile_change_action_redirects(self):
        request = self.factory.get("/")
        request.user = self.user
        request.session = self.client.session
        from django.contrib.messages.storage.fallback import FallbackStorage

        request._messages = FallbackStorage(request)
        response = self.admin.my_profile_action(request, self.inbox)
        self.assertEqual(response.status_code, 302)
        opts = self.admin.model._meta
        expected = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_change", args=[self.inbox.pk]
        )
        self.assertEqual(response.url, expected)

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

    def test_test_collectors_actions(self):
        collector = EmailCollector.objects.create(inbox=self.inbox)
        request = self.factory.post(
            "/", {"action": "test_collectors", "_selected_action": [self.inbox.pk]}
        )
        request.user = self.user
        request.session = self.client.session
        from django.contrib.messages.storage.fallback import FallbackStorage

        request._messages = FallbackStorage(request)
        with patch.object(EmailCollector, "collect") as mock_collect:
            self.admin.test_collectors(
                request, EmailInbox.objects.filter(pk=self.inbox.pk)
            )
            mock_collect.assert_called_once_with(limit=1)
        messages = list(request._messages)
        self.assertEqual(len(messages), 1)

        request2 = self.factory.post("/", {"_action": "test_collectors_action"})
        request2.user = self.user
        request2.session = self.client.session
        request2._messages = FallbackStorage(request2)
        with patch.object(EmailCollector, "collect") as mock_collect2:
            self.admin.test_collectors_action(request2, self.inbox)
            mock_collect2.assert_called_once_with(limit=1)
        messages2 = list(request2._messages)
        self.assertEqual(len(messages2), 1)


class EmailCollectorInlineTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="manager", email="a@example.com", password="pwd"
        )
        self.factory = RequestFactory()
        self.admin = EmailInboxAdmin(EmailInbox, AdminSite())

    def test_can_add_multiple_collectors(self):
        data = {
            "user": self.user.pk,
            "host": "imap.test",
            "port": 993,
            "username": "u",
            "password": "p",
            "protocol": EmailInbox.IMAP,
            "use_ssl": "on",
            "collectors-TOTAL_FORMS": "2",
            "collectors-INITIAL_FORMS": "0",
            "collectors-MIN_NUM_FORMS": "0",
            "collectors-MAX_NUM_FORMS": "1000",
            "collectors-0-id": "",
            "collectors-0-subject": "s1",
            "collectors-0-sender": "",
            "collectors-1-id": "",
            "collectors-1-subject": "s2",
            "collectors-1-sender": "",
            "_save": "Save",
        }
        request = self.factory.post("/", data)
        request.user = self.user
        request.session = self.client.session
        from django.contrib.messages.storage.fallback import FallbackStorage

        request._messages = FallbackStorage(request)
        request._dont_enforce_csrf_checks = True
        with patch.object(EmailInboxAdmin, "log_addition"):
            response = self.admin.add_view(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EmailCollector.objects.count(), 2)
        inbox = EmailInbox.objects.get()
        self.assertEqual(inbox.collectors.count(), 2)


class EmailCollectorAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="manager", email="a@example.com", password="pwd"
        )
        self.factory = RequestFactory()
        self.inbox = EmailInbox.objects.create(
            user=self.user,
            host="imap.test",
            port=993,
            username="u",
            password="p",
            protocol=EmailInbox.IMAP,
            use_ssl=True,
        )
        self.admin = admin.site._registry[EmailCollector]

    def test_collector_registered_standalone(self):
        self.assertIn(EmailCollector, admin.site._registry)

    def test_changelist_and_change_view(self):
        collector = EmailCollector.objects.create(inbox=self.inbox)
        request = self.factory.get("/")
        request.user = self.user
        request.session = self.client.session
        response = self.admin.changelist_view(request)
        self.assertEqual(response.status_code, 200)

        request2 = self.factory.get("/")
        request2.user = self.user
        request2.session = self.client.session
        response2 = self.admin.changeform_view(request2, str(collector.pk))
        self.assertEqual(response2.status_code, 200)

    def test_preview_action_renders_messages(self):
        collector = EmailCollector.objects.create(inbox=self.inbox, name="Invoices")
        request = self.factory.post(
            "/",
            {"action": "preview_messages", "_selected_action": [collector.pk]},
        )
        request.user = self.user
        request.session = self.client.session
        with patch.object(
            EmailCollector,
            "search_messages",
            return_value=[
                {
                    "subject": "Invoice 1",
                    "from": "billing@example.com",
                    "date": "2024-04-01",
                    "body": "Thanks for your business",
                }
            ],
        ) as mock_search:
            response = self.admin.preview_messages(
                request, EmailCollector.objects.filter(pk=collector.pk)
            )
        self.assertEqual(response.status_code, 200)
        content = response.render().content.decode()
        self.assertIn("Invoices", content)
        self.assertIn("Invoice 1", content)
        mock_search.assert_called_once_with(limit=5)

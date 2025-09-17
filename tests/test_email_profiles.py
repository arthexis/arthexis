from django.contrib.auth import get_user_model
from django.test import TestCase

from django.urls import reverse

from core.admin import USER_PROFILE_INLINES
from core.models import EmailInbox
from nodes.models import EmailOutbox


class EmailProfileIntegrationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pwd"
        )

    def test_email_inbox_available_via_profile_lookup(self):
        inbox = EmailInbox.objects.create(
            user=self.user,
            host="imap.test",
            port=993,
            username="inbox",
            password="secret",
            protocol=EmailInbox.IMAP,
            use_ssl=True,
        )

        self.assertEqual(self.user.get_profile(EmailInbox), inbox)

    def test_email_outbox_available_via_profile_lookup(self):
        outbox = EmailOutbox.objects.create(
            user=self.user,
            host="smtp.test",
            port=587,
            username="outbox",
            password="secret",
        )

        self.assertEqual(self.user.get_profile(EmailOutbox), outbox)

    def test_email_outbox_clean_allows_ownerless_entries(self):
        outbox = EmailOutbox(
            host="smtp.other",
            port=587,
            username="service",
            password="secret",
        )

        # Should not raise when saving without a profile owner.
        outbox.full_clean()


class UserAdminSidebarTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pwd"
        )

    def test_profile_inlines_include_email_models(self):
        inline_models = {inline.model for inline in USER_PROFILE_INLINES}
        self.assertIn(EmailInbox, inline_models)
        self.assertIn(EmailOutbox, inline_models)

    def test_change_form_contains_section_sidebar(self):
        self.client.force_login(self.user)
        url = reverse("admin:teams_user_change", args=[self.user.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('id="user-changeform-sections"', content)

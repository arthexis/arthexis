from django.contrib.auth import get_user_model
from django.test import TestCase

from django.urls import reverse

from core.admin import USER_PROFILE_INLINES
from teams.models import EmailInbox, EmailOutbox


class EmailProfileIntegrationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="manager", email="manager@example.com", password="pwd"
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

    def test_admin_user_can_have_profiles(self):
        User = get_user_model()
        admin_user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pwd"
        )
        inbox = EmailInbox(
            user=admin_user,
            host="imap.test",
            port=993,
            username="restricted",
            password="secret",
            protocol=EmailInbox.IMAP,
            use_ssl=True,
        )
        # Should validate successfully for the admin user.
        inbox.full_clean()

    def test_email_outbox_clean_allows_ownerless_entries(self):
        outbox = EmailOutbox(
            host="smtp.other",
            port=587,
            username="service",
            password="secret",
        )

        # Should not raise when saving without a profile owner.
        outbox.full_clean()


class UserAdminProfileTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="manager", email="manager@example.com", password="pwd"
        )

    def test_profile_inlines_include_email_models(self):
        inline_models = {inline.model for inline in USER_PROFILE_INLINES}
        self.assertIn(EmailInbox, inline_models)
        self.assertIn(EmailOutbox, inline_models)

    def test_change_form_omits_section_sidebar(self):
        self.client.force_login(self.user)
        url = reverse("admin:teams_user_change", args=[self.user.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn('id="user-changeform-sections"', content)

    def test_admin_index_lists_profile_models(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        profile_urls = [
            reverse("admin:teams_odooprofile_changelist"),
            reverse("admin:teams_releasemanager_changelist"),
            reverse("admin:teams_emailinbox_changelist"),
            reverse("admin:teams_emailoutbox_changelist"),
        ]
        for url in profile_urls:
            with self.subTest(url=url):
                self.assertIn(f'href="{url}"', content)

    def test_change_form_shows_profiles_for_admin_user(self):
        User = get_user_model()
        admin_user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pwd"
        )
        self.client.force_login(admin_user)
        url = reverse("admin:teams_user_change", args=[admin_user.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("profile-inline-heading", content)

    def test_change_form_profile_inline_prefixes(self):
        User = get_user_model()
        target = User.objects.create_user("profileprefix", password="pwd")
        self.client.force_login(self.user)
        url = reverse("admin:teams_user_change", args=[target.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        prefixes = (
            "odooprofile",
            "emailinbox",
            "emailoutbox",
            "releasemanager",
        )
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                self.assertIn(f'data-inline-prefix="{prefix}"', content)
                self.assertIn(f'name="{prefix}-TOTAL_FORMS"', content)

    def test_change_form_uses_profile_user_datum_controls(self):
        User = get_user_model()
        target = User.objects.create_user("profiledatum", password="pwd")
        self.client.force_login(self.user)
        url = reverse("admin:teams_user_change", args=[target.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn('name="_user_datum"', content)
        prefixes = (
            "odooprofile",
            "emailinbox",
            "emailoutbox",
            "releasemanager",
        )
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                self.assertIn(f'name="{prefix}-0-user_datum"', content)

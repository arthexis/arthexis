import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from django.core.management import call_command
call_command("migrate", run_syncdb=True, verbosity=0)
from django.conf import settings
settings.ALLOWED_HOSTS=["testserver"]



from django.test import Client, TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from unittest.mock import patch

from .admin import BskyAccountAdminForm
from .models import BskyAccount
from .services import post_from_domain, post_from_user, register_account


User = get_user_model()


class BskyServiceTests(TestCase):
    def test_register_creates_account(self):
        user = User.objects.create_user("alice", password="x")
        with patch("atproto.Client") as MockClient:
            client = MockClient.return_value
            client.login.return_value = None
            register_account(user, "alice.bsky.social", "app-pw")

        acc = BskyAccount.objects.get(user=user)
        self.assertEqual(acc.handle, "alice.bsky.social")
        self.assertEqual(acc.app_password, "app-pw")

    def test_post_from_user_logs_in_and_posts(self):
        user = User.objects.create_user("bob", password="x")
        BskyAccount.objects.create(user=user, handle="bob.bsky.social", app_password="pw")
        with patch("atproto.Client") as MockClient:
            client = MockClient.return_value
            post_from_user(user, "hello")
            client.login.assert_called_once_with("bob.bsky.social", "pw")
            client.send_post.assert_called_once_with("hello")

    @override_settings(BSKY_HANDLE="domain", BSKY_APP_PASSWORD="secret")
    def test_post_from_domain_uses_settings(self):
        with patch("atproto.Client") as MockClient:
            client = MockClient.return_value
            post_from_domain("hi")
            client.login.assert_called_once_with("domain", "secret")
            client.send_post.assert_called_once_with("hi")


class BskyAdminFormTests(TestCase):
    def test_form_validates_credentials(self):
        user = User.objects.create_user("eve", password="x")
        form_data = {"user": user.pk, "handle": "eve.bsky.social", "app_password": "pw"}
        with patch("atproto.Client") as MockClient:
            MockClient.return_value.login.return_value = None
            form = BskyAccountAdminForm(data=form_data)
            self.assertTrue(form.is_valid())
            MockClient.return_value.login.assert_called_once_with("eve.bsky.social", "pw")

    def test_form_rejects_bad_credentials(self):
        user = User.objects.create_user("mallory", password="x")
        form_data = {"user": user.pk, "handle": "mal.bsky.social", "app_password": "bad"}
        with patch("atproto.Client") as MockClient:
            MockClient.return_value.login.side_effect = Exception("bad creds")
            form = BskyAccountAdminForm(data=form_data)
            self.assertFalse(form.is_valid())


class BskyAdminActionTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="bsky-admin",
            password="secret",
            email="admin@arthexis.com",
        )
        self.client.force_login(self.admin)
        self.account = BskyAccount.objects.create(
            user=self.admin, handle="bsky-admin.bsky.social", app_password="pw"
        )

    @patch("atproto.Client")
    def test_admin_action(self, MockClient):
        MockClient.return_value.login.return_value = None
        url = reverse("admin:integrator_bskyaccount_changelist")
        resp = self.client.post(
            url,
            {"action": "test_credentials", "_selected_action": [self.account.pk]},
        )
        self.assertEqual(resp.status_code, 302)
        MockClient.return_value.login.assert_called_once_with(
            "bsky-admin.bsky.social", "pw"
        )

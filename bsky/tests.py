from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from unittest.mock import patch

from .models import BskyAccount
from .services import post_from_domain, post_from_user, register_account


User = get_user_model()


class BskyServiceTests(TestCase):
    def test_register_creates_account(self):
        user = User.objects.create_user("alice", password="x")
        with patch("bsky.services.Client") as MockClient:
            client = MockClient.return_value
            client.login.return_value = None
            register_account(user, "alice.bsky.social", "app-pw")

        acc = BskyAccount.objects.get(user=user)
        self.assertEqual(acc.handle, "alice.bsky.social")
        self.assertEqual(acc.app_password, "app-pw")

    def test_post_from_user_logs_in_and_posts(self):
        user = User.objects.create_user("bob", password="x")
        BskyAccount.objects.create(user=user, handle="bob.bsky.social", app_password="pw")
        with patch("bsky.services.Client") as MockClient:
            client = MockClient.return_value
            post_from_user(user, "hello")
            client.login.assert_called_once_with("bob.bsky.social", "pw")
            client.send_post.assert_called_once_with("hello")

    @override_settings(BSKY_HANDLE="domain", BSKY_APP_PASSWORD="secret")
    def test_post_from_domain_uses_settings(self):
        with patch("bsky.services.Client") as MockClient:
            client = MockClient.return_value
            post_from_domain("hi")
            client.login.assert_called_once_with("domain", "secret")
            client.send_post.assert_called_once_with("hi")

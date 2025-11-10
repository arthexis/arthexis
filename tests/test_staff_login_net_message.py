from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from core import user_data
from nodes.models import NetMessage


class StaffLoginNetMessageTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.User = get_user_model()
        self._original_shared_loaded = user_data._shared_fixtures_loaded
        user_data._shared_fixtures_loaded = False

    def tearDown(self):
        user_data._shared_fixtures_loaded = self._original_shared_loaded

    def test_staff_login_does_not_send_net_message(self):
        user = self.User.objects.create_user(
            username="staffer", password="pwd", is_staff=True
        )
        request = self.factory.get("/", REMOTE_ADDR="198.51.100.10")

        user_data._on_login(sender=self.User, request=request, user=user)

        self.assertFalse(
            NetMessage.objects.filter(subject__startswith="login ").exists()
        )

    def test_forwarded_for_ip_does_not_send_net_message(self):
        user = self.User.objects.create_user(
            username="forwarded", password="pwd", is_staff=True
        )
        request = self.factory.get(
            "/",
            HTTP_X_FORWARDED_FOR="198.51.100.1, 203.0.113.2",
            REMOTE_ADDR="203.0.113.5",
        )

        user_data._on_login(sender=self.User, request=request, user=user)

        self.assertFalse(
            NetMessage.objects.filter(subject__startswith="login ").exists()
        )

    def test_non_staff_login_does_not_send_net_message(self):
        user = self.User.objects.create_user(username="member", password="pwd")
        request = self.factory.get("/", REMOTE_ADDR="203.0.113.5")

        user_data._on_login(sender=self.User, request=request, user=user)

        self.assertFalse(
            NetMessage.objects.filter(subject__startswith="login ").exists()
        )

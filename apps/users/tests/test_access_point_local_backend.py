from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from apps.users.backends import AccessPointLocalUserBackend


class AccessPointLocalUserBackendTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.backend = AccessPointLocalUserBackend()

    def _request(self, remote_addr: str):
        return self.factory.post(
            "/login/",
            {
                "username": "ap-user",
                "password": "anything",
            },
            HTTP_HOST="127.0.0.1",
            REMOTE_ADDR=remote_addr,
        )

    def test_authenticates_passwordless_local_user_from_matching_ipv4_prefix(self):
        user = get_user_model().objects.create_user(
            username="ap-user",
            email="ap-user@example.com",
            is_staff=False,
            is_superuser=False,
            allow_local_network_passwordless_login=True,
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])

        request = self._request("127.0.0.1")

        authenticated = self.backend.authenticate(
            request,
            username="ap-user",
            password="totally-wrong",
        )

        assert authenticated is not None
        assert authenticated.pk == user.pk

    def test_rejects_user_when_passwordless_flag_is_disabled(self):
        user = get_user_model().objects.create_user(
            username="no-ap",
            email="no-ap@example.com",
            is_staff=False,
            is_superuser=False,
            allow_local_network_passwordless_login=False,
        )
        request = self._request("127.0.0.1")

        authenticated = self.backend.authenticate(
            request,
            username=user.username,
            password="anything",
        )

        assert authenticated is None

    def test_rejects_non_loopback_ipv6_request(self):
        user = get_user_model().objects.create_user(
            username="ap-v6",
            email="ap-v6@example.com",
            is_staff=False,
            is_superuser=False,
            allow_local_network_passwordless_login=True,
        )

        request = self._request("2001:db8::10")

        authenticated = self.backend.authenticate(
            request,
            username=user.username,
            password="anything",
        )

        assert authenticated is None

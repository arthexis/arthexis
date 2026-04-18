import ipaddress

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

    def test_authenticates_local_user_with_valid_password_from_matching_ipv4_prefix(self):
        user = get_user_model().objects.create_user(
            username="ap-user",
            email="ap-user@example.com",
            password="correct-password",
            is_staff=False,
            is_superuser=False,
            allow_local_network_passwordless_login=True,
        )

        request = self._request("127.0.0.1")

        authenticated = self.backend.authenticate(
            request,
            username="ap-user",
            password="correct-password",
        )

        assert authenticated is not None
        assert authenticated.pk == user.pk

    def test_rejects_local_user_with_invalid_password(self):
        user = get_user_model().objects.create_user(
            username="ap-user-invalid-password",
            email="ap-user-invalid-password@example.com",
            password="correct-password",
            is_staff=False,
            is_superuser=False,
            allow_local_network_passwordless_login=True,
        )
        request = self._request("127.0.0.1")

        authenticated = self.backend.authenticate(
            request,
            username=user.username,
            password="wrong-password",
        )

        assert authenticated is None

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

    def test_rejects_inactive_access_point_user(self):
        user = get_user_model().objects.create_user(
            username="inactive-ap",
            email="inactive-ap@example.com",
            is_active=False,
            is_staff=False,
            is_superuser=False,
            allow_local_network_passwordless_login=True,
        )
        request = self._request("127.0.0.1")

        authenticated = self.backend.authenticate(
            request,
            username=user.username,
            password="anything",
        )

        assert authenticated is None

    def test_rejects_public_ipv4_request(self):
        user = get_user_model().objects.create_user(
            username="public-ap",
            email="public-ap@example.com",
            is_staff=False,
            is_superuser=False,
            allow_local_network_passwordless_login=True,
        )
        self.backend._LOCAL_IPS = (ipaddress.ip_address("8.8.1.10"),)
        request = self._request("8.8.200.11")

        authenticated = self.backend.authenticate(
            request,
            username=user.username,
            password="anything",
        )

        assert authenticated is None

    def test_rejects_private_ipv4_outside_local_prefix(self):
        user = get_user_model().objects.create_user(
            username="far-ap",
            email="far-ap@example.com",
            is_staff=False,
            is_superuser=False,
            allow_local_network_passwordless_login=True,
        )
        self.backend._LOCAL_IPS = (ipaddress.ip_address("10.50.0.1"),)
        request = self._request("10.99.0.1")

        authenticated = self.backend.authenticate(
            request,
            username=user.username,
            password="anything",
        )

        assert authenticated is None

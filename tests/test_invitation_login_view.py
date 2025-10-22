from unittest.mock import Mock, patch

import pytest

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.models import InviteLead


class InvitationLoginViewTests(TestCase):
    def setUp(self):
        self.User = get_user_model()

    def _invitation_url(self, user):
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        return reverse("pages:invitation-login", args=[uidb64, token])

    def test_post_with_password_activates_user_and_updates_password(self):
        user = self.User.objects.create_user(
            username="member",
            email="member@example.com",
            password="old-secret",
            is_active=False,
        )
        old_hash = user.password

        response = self.client.post(
            self._invitation_url(user),
            {"new_password1": "new-secret", "new_password2": "new-secret"},
        )

        self.assertRedirects(response, "/")
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertNotEqual(user.password, old_hash)
        self.assertTrue(user.check_password("new-secret"))
        session = self.client.session
        self.assertEqual(session.get("_auth_user_id"), str(user.pk))

    def test_post_without_password_activates_user_and_keeps_existing_password(self):
        user = self.User.objects.create_user(
            username="nopass",
            email="nopass@example.com",
            password="existing-secret",
            is_active=False,
        )
        old_hash = user.password

        response = self.client.post(self._invitation_url(user), {})

        self.assertRedirects(response, "/")
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(user.password, old_hash)
        self.assertTrue(user.check_password("existing-secret"))
        session = self.client.session
        self.assertEqual(session.get("_auth_user_id"), str(user.pk))

    def test_staff_user_redirects_to_admin_after_login(self):
        user = self.User.objects.create_user(
            username="staff",
            email="staff@example.com",
            password="old-staff",
            is_active=False,
            is_staff=True,
        )

        response = self.client.post(
            self._invitation_url(user),
            {"new_password1": "staff-secret", "new_password2": "staff-secret"},
        )

        self.assertRedirects(response, reverse("admin:index"))
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("staff-secret"))
        session = self.client.session
        self.assertEqual(session.get("_auth_user_id"), str(user.pk))

    @pytest.mark.feature("ap-router")
    @patch("pages.views.public_wifi.resolve_mac_address", return_value="aa:bb:cc:dd:ee:ff")
    @patch("pages.views.public_wifi.grant_public_access")
    def test_wifi_provisioning_attempted_when_feature_enabled(
        self, mock_grant_public_access, mock_resolve_mac
    ):
        user = self.User.objects.create_user(
            username="wifi",
            email="wifi@example.com",
            password="wifi-secret",
            is_active=False,
        )
        mock_node = Mock()
        mock_node.has_feature.return_value = True
        with patch("pages.views.Node.get_local", return_value=mock_node) as mock_get_local:
            response = self.client.post(self._invitation_url(user), {})

        self.assertRedirects(response, "/")
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(mock_get_local.called)
        self.assertTrue(mock_resolve_mac.called)
        mock_grant_public_access.assert_called_once_with(user, "aa:bb:cc:dd:ee:ff")

    @pytest.mark.feature("ap-router")
    @patch("pages.views.public_wifi.resolve_mac_address", return_value=None)
    @patch("pages.views.public_wifi.grant_public_access")
    def test_wifi_provisioning_uses_fallback_mac_when_available(
        self, mock_grant_public_access, mock_resolve_mac
    ):
        user = self.User.objects.create_user(
            username="wifi-fallback",
            email="wifi-fallback@example.com",
            password="wifi-secret",
            is_active=False,
        )
        InviteLead.objects.create(email=user.email, mac_address="ff:ee:dd:cc:bb:aa")
        mock_node = Mock()
        mock_node.has_feature.return_value = True
        with patch("pages.views.Node.get_local", return_value=mock_node) as mock_get_local:
            response = self.client.post(self._invitation_url(user), {})

        self.assertRedirects(response, "/")
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(mock_get_local.called)
        self.assertTrue(mock_resolve_mac.called)
        mock_grant_public_access.assert_called_once_with(user, "ff:ee:dd:cc:bb:aa")
        session = self.client.session
        self.assertEqual(session.get("_auth_user_id"), str(user.pk))

    def test_invalid_token_returns_bad_request_without_user_mutation(self):
        user = self.User.objects.create_user(
            username="invalid",
            email="invalid@example.com",
            password="invalid-secret",
            is_active=False,
        )
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        url = reverse("pages:invitation-login", args=[uidb64, "bogus-token"])
        old_hash = user.password

        response = self.client.post(
            url,
            {"new_password1": "changed", "new_password2": "changed"},
        )

        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertEqual(user.password, old_hash)
        self.assertNotIn("_auth_user_id", self.client.session)

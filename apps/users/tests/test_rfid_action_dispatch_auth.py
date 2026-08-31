from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.cards.actions import RFIDActionResult
from apps.cards.models import RFID
from apps.energy.models import CustomerAccount
from apps.users.backends import RFIDBackend


class RFIDActionDispatchAuthTests(TestCase):
    def test_authentication_succeeds_without_shell_execution(self):
        user = get_user_model().objects.create_user(
            username="action-success-user",
            password="test-pass-123",
        )
        tag = RFID.objects.create(
            rfid="1122AABB",
            allowed=True,
            external_command="legacy shell command",
            post_auth_command="legacy post shell command",
            validation_action="LOG",
            post_auth_action="NOOP",
        )
        account = CustomerAccount.objects.create(name="ACTION-SUCCESS", user=user)
        account.rfids.add(tag)

        backend = RFIDBackend()
        with patch("apps.users.backends.dispatch_rfid_action") as action_mock:
            action_mock.side_effect = [
                RFIDActionResult(success=True, error=""),
                RFIDActionResult(success=True, error=""),
            ]
            authenticated = backend.authenticate(None, rfid=tag.rfid)

        self.assertIsNotNone(authenticated)
        self.assertEqual(authenticated.pk, user.pk)
        self.assertEqual(action_mock.call_count, 2)

    def test_authentication_fails_when_validation_action_rejects(self):
        user = get_user_model().objects.create_user(
            username="action-failure-user",
            password="test-pass-123",
        )
        tag = RFID.objects.create(
            rfid="AABB3344",
            allowed=True,
            external_command="legacy shell command",
            validation_action="REJECT",
        )
        account = CustomerAccount.objects.create(name="ACTION-FAILURE", user=user)
        account.rfids.add(tag)

        backend = RFIDBackend()
        with patch("apps.users.backends.dispatch_rfid_action") as action_mock:
            action_mock.return_value = RFIDActionResult(success=False, error="rejected")
            authenticated = backend.authenticate(None, rfid=tag.rfid)

        self.assertIsNone(authenticated)
        action_mock.assert_called_once()

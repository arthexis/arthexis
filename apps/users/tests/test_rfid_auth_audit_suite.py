"""Regression coverage for RFID authentication audit suite logging."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.cards.models import RFID, RFIDAttempt
from apps.features.models import Feature
from apps.users.backends import RFID_AUTH_AUDIT_FEATURE_SLUG


class RFIDAuthAuditSuiteTests(TestCase):
    """Validate RFID auth attempt persistence when the audit suite is toggled."""

    def setUp(self) -> None:
        """Create an HTTP client for RFID login requests."""

        self.client = Client()

    @staticmethod
    def _set_audit_feature(*, enabled: bool) -> None:
        """Create or update the RFID auth audit suite feature state."""

        Feature.objects.update_or_create(
            slug=RFID_AUTH_AUDIT_FEATURE_SLUG,
            defaults={
                "display": "RFID Auth Audit",
                "is_enabled": enabled,
                "source": Feature.Source.CUSTOM,
            },
        )

    def test_rfid_login_records_accepted_auth_attempt(self) -> None:
        """Regression: successful RFID logins must record accepted auth attempts."""

        self._set_audit_feature(enabled=True)
        user_model = get_user_model()
        tag = RFID.objects.create(rfid="A1B2C3D4", allowed=True)
        user = user_model.objects.create_user(
            username="rfid_user",
            password="password123",
            login_rfid=tag,
        )

        response = self.client.post(
            reverse("rfid-login"),
            data='{"rfid":"a1b2c3d4"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        attempt = RFIDAttempt.objects.get(source=RFIDAttempt.Source.AUTH)
        self.assertEqual(attempt.status, RFIDAttempt.Status.ACCEPTED)
        self.assertTrue(attempt.authenticated)
        self.assertEqual(attempt.rfid, "A1B2C3D4")
        self.assertEqual(attempt.label_id, tag.pk)
        self.assertEqual(attempt.payload.get("auth_path"), "login_rfid")
        self.assertIsNone(attempt.payload.get("reason_code"))
        self.assertIsNone(attempt.account_id)
        self.assertEqual(user.pk, response.json()["id"])

    def test_rfid_login_records_rejected_reason_for_blocked_tag(self) -> None:
        """Regression: blocked RFID tags should emit a rejected auth attempt reason."""

        self._set_audit_feature(enabled=True)
        tag = RFID.objects.create(rfid="DEADBEEF", allowed=False)

        response = self.client.post(
            reverse("rfid-login"),
            data='{"rfid":"deadbeef"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        attempt = RFIDAttempt.objects.get(source=RFIDAttempt.Source.AUTH)
        self.assertEqual(attempt.status, RFIDAttempt.Status.REJECTED)
        self.assertFalse(attempt.authenticated)
        self.assertEqual(
            attempt.payload.get("reason_code"),
            RFIDAttempt.Reason.TAG_NOT_ALLOWED,
        )
        self.assertEqual(attempt.label_id, tag.pk)

    def test_rfid_login_skips_auth_audit_when_suite_feature_disabled(self) -> None:
        """Regression: disabling suite feature must stop RFID auth attempt persistence."""

        self._set_audit_feature(enabled=False)
        RFID.objects.create(rfid="FEEDBEEF", allowed=False)

        response = self.client.post(
            reverse("rfid-login"),
            data='{"rfid":"feedbeef"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            RFIDAttempt.objects.filter(source=RFIDAttempt.Source.AUTH).count(),
            0,
        )

    def test_rfid_login_success_ignores_legacy_shell_command_fields(self) -> None:
        """Regression: legacy command text must not influence authentication flow."""

        self._set_audit_feature(enabled=True)
        user_model = get_user_model()
        tag = RFID.objects.create(
            rfid="ABCDEF12",
            allowed=True,
            external_command="exit 1",
            post_auth_command="echo ignored",
        )
        user = user_model.objects.create_user(
            username="legacy_command_user",
            password="password123",
            login_rfid=tag,
        )

        response = self.client.post(
            reverse("rfid-login"),
            data='{"rfid":"abcdef12"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        attempt = RFIDAttempt.objects.get(source=RFIDAttempt.Source.AUTH)
        self.assertEqual(attempt.status, RFIDAttempt.Status.ACCEPTED)
        self.assertEqual(user.pk, response.json()["id"])

    def test_rfid_login_rejects_when_allowlisted_pre_auth_action_denies(self) -> None:
        """Regression: pre-auth action hooks should reject authentication safely."""

        self._set_audit_feature(enabled=True)
        user_model = get_user_model()
        tag = RFID.objects.create(
            rfid="CAFEBABE",
            allowed=True,
            pre_auth_action="deny",
        )
        user_model.objects.create_user(
            username="deny_action_user",
            password="password123",
            login_rfid=tag,
        )

        response = self.client.post(
            reverse("rfid-login"),
            data='{"rfid":"cafebabe"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        attempt = RFIDAttempt.objects.get(source=RFIDAttempt.Source.AUTH)
        self.assertEqual(attempt.status, RFIDAttempt.Status.REJECTED)
        self.assertEqual(
            attempt.payload.get("reason_code"),
            RFIDAttempt.Reason.EXTERNAL_COMMAND_ERROR,
        )
        self.assertEqual(attempt.payload.get("pre_auth_action"), "deny")

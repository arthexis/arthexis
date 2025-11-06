import hashlib
import hmac
from datetime import datetime
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import OpenPayProfile, User


class OpenPayProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="merchant", password="test-pass")

    def _create_profile(self, **overrides):
        defaults = {
            "user": self.user,
            "merchant_id": "m12345",
            "private_key": "sk_test",
            "public_key": "pk_test",
            "webhook_secret": "whsec",
        }
        defaults.update(overrides)
        return OpenPayProfile.objects.create(**defaults)

    def test_clear_verification_on_credentials_change(self):
        profile = self._create_profile()
        profile.verified_on = timezone.now()
        profile.verification_reference = "ok"
        profile.save(update_fields=["verified_on", "verification_reference"])

        profile.merchant_id = "m67890"
        profile.save()

        profile.refresh_from_db()
        self.assertIsNone(profile.verified_on)
        self.assertEqual(profile.verification_reference, "")

    def test_clear_verification_on_paypal_change(self):
        profile = self._create_profile(
            paypal_client_id="paypal-id",
            paypal_client_secret="paypal-secret",
            default_processor=OpenPayProfile.PROCESSOR_PAYPAL,
        )
        profile.verified_on = timezone.now()
        profile.verification_reference = "ok"
        profile.save(update_fields=["verified_on", "verification_reference"])

        profile.paypal_client_secret = "new-secret"
        profile.save()

        profile.refresh_from_db()
        self.assertIsNone(profile.verified_on)
        self.assertEqual(profile.verification_reference, "")

    @mock.patch("core.models.timezone.now")
    @mock.patch("core.models.requests.post")
    @mock.patch("core.models.requests.get")
    def test_verify_updates_metadata(self, mock_get, mock_post, mock_now):
        profile = self._create_profile()
        expected_time = timezone.make_aware(datetime(2024, 1, 1, 12, 0, 0))
        mock_now.return_value = expected_time

        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = {"status": "available"}
        mock_get.return_value = response

        result = profile.verify()

        self.assertTrue(result)
        profile.refresh_from_db()
        self.assertEqual(profile.verification_reference, "available")
        self.assertEqual(profile.verified_on, expected_time)

        mock_get.assert_called_once()
        called_url = mock_get.call_args.kwargs.get("url") or mock_get.call_args.args[0]
        self.assertEqual(
            called_url,
            f"{OpenPayProfile.SANDBOX_API_URL}/{profile.merchant_id}/charges",
        )
        self.assertEqual(mock_get.call_args.kwargs["auth"], (profile.private_key, ""))
        self.assertEqual(mock_get.call_args.kwargs["params"], {"limit": 1})
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 10)
        mock_post.assert_not_called()

    @mock.patch("core.models.requests.post")
    @mock.patch("core.models.requests.get")
    def test_verify_failure_clears_verification(self, mock_get, mock_post):
        profile = self._create_profile()
        profile.verified_on = timezone.now()
        profile.verification_reference = "existing"
        profile.save(update_fields=["verified_on", "verification_reference"])

        response = mock.Mock()
        response.status_code = 401
        response.json.return_value = {"status": "denied"}
        mock_get.return_value = response

        with self.assertRaises(ValidationError):
            profile.verify()

        profile.refresh_from_db()
        self.assertIsNone(profile.verified_on)
        self.assertEqual(profile.verification_reference, "")
        mock_post.assert_not_called()

    @mock.patch("core.models.requests.post")
    @mock.patch("core.models.requests.get")
    def test_verify_falls_back_to_paypal(self, mock_get, mock_post):
        profile = self._create_profile(
            paypal_client_id="paypal-id",
            paypal_client_secret="paypal-secret",
            default_processor=OpenPayProfile.PROCESSOR_OPENPAY,
        )

        failure = mock.Mock()
        failure.status_code = 401
        failure.json.return_value = {"status": "denied"}
        mock_get.return_value = failure

        success = mock.Mock()
        success.status_code = 200
        success.json.return_value = {"scope": "payments"}
        mock_post.return_value = success

        result = profile.verify()

        self.assertTrue(result)
        mock_get.assert_called_once()
        mock_post.assert_called_once()
        profile.refresh_from_db()
        self.assertIn("PayPal", profile.verification_reference)

    @mock.patch("core.models.requests.post")
    def test_verify_uses_paypal_when_default(self, mock_post):
        profile = self._create_profile(
            merchant_id="",
            private_key="",
            public_key="",
            default_processor=OpenPayProfile.PROCESSOR_PAYPAL,
            paypal_client_id="paypal-id",
            paypal_client_secret="paypal-secret",
        )

        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = {"scope": "payments"}
        mock_post.return_value = response

        with mock.patch("core.models.requests.get") as mock_get:
            result = profile.verify()

        self.assertTrue(result)
        mock_get.assert_not_called()
        mock_post.assert_called_once()
        profile.refresh_from_db()
        self.assertEqual(profile.verification_reference, "PayPal: payments")

    def test_sign_webhook_uses_hmac(self):
        profile = self._create_profile(webhook_secret="secret")
        payload = "{\"id\":\"evt_1\"}"
        signature = profile.sign_webhook(payload, timestamp="1713387600")

        expected = hmac.new(
            b"secret",
            b"1713387600." + payload.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()
        self.assertEqual(signature, expected)

        profile.webhook_secret = ""
        with self.assertRaises(ValueError):
            profile.sign_webhook(payload)

    def test_environment_helpers(self):
        profile = self._create_profile()
        self.assertTrue(profile.is_sandbox())
        self.assertEqual(
            profile.build_api_url("charges"),
            f"{OpenPayProfile.SANDBOX_API_URL}/{profile.merchant_id}/charges",
        )

        profile.verified_on = timezone.now()
        profile.verification_reference = "cached"
        profile.save(update_fields=["verified_on", "verification_reference"])

        profile.use_production()
        profile.save()
        profile.refresh_from_db()
        self.assertTrue(profile.is_production)
        self.assertFalse(profile.is_sandbox())
        self.assertEqual(
            profile.get_api_base_url(),
            OpenPayProfile.PRODUCTION_API_URL,
        )
        self.assertEqual(
            profile.build_api_url(),
            f"{OpenPayProfile.PRODUCTION_API_URL}/{profile.merchant_id}",
        )
        self.assertIsNone(profile.verified_on)
        self.assertEqual(profile.verification_reference, "")

        profile.set_environment(production=False)
        self.assertFalse(profile.is_production)
        self.assertEqual(
            profile.get_paypal_api_base_url(),
            OpenPayProfile.PAYPAL_SANDBOX_API_URL,
        )

        profile.paypal_is_production = True
        self.assertEqual(
            profile.get_paypal_api_base_url(),
            OpenPayProfile.PAYPAL_PRODUCTION_API_URL,
        )

    def test_iter_processors_ordering(self):
        profile = self._create_profile()
        self.assertEqual(
            list(profile.iter_processors()),
            [OpenPayProfile.PROCESSOR_OPENPAY],
        )

        profile.paypal_client_id = "paypal-id"
        profile.paypal_client_secret = "paypal-secret"
        self.assertEqual(
            list(profile.iter_processors()),
            [OpenPayProfile.PROCESSOR_OPENPAY, OpenPayProfile.PROCESSOR_PAYPAL],
        )

        profile.default_processor = OpenPayProfile.PROCESSOR_PAYPAL
        self.assertEqual(
            list(profile.iter_processors()),
            [OpenPayProfile.PROCESSOR_PAYPAL, OpenPayProfile.PROCESSOR_OPENPAY],
        )

    def test_verify_without_credentials(self):
        profile = self._create_profile(
            merchant_id="",
            private_key="",
            public_key="",
            webhook_secret="",
        )

        with self.assertRaises(ValidationError) as exc:
            profile.verify()

        self.assertIn("No payment processors", str(exc.exception))

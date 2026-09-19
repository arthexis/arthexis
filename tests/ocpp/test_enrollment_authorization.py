"""Enrollment and authorization-policy contracts for retained OCPP versions."""

from asgiref.sync import async_to_sync
from django.contrib.auth.hashers import make_password
from django.test import TestCase, override_settings

from apps.cards.models import AuthorizationAttempt, CardCredential
from apps.ocpp.models import Charger, OcppTransaction
from apps.ocpp.protocol.v16.inbound import InboundActions as Inbound16Actions
from apps.ocpp.protocol.v201.inbound import InboundActions as Inbound201Actions
from apps.ocpp.services.authorization import authorize_id_tag
from apps.ocpp.transport.connection import load_or_enroll_charger
from tests.ocpp.builders import charger


class ChargerEnrollmentTests(TestCase):
    def test_unknown_identity_requires_a_valid_enrollment_credential(self) -> None:
        with override_settings(OCPP_ENROLLMENT_TOKEN_HASH=make_password("enroll")):
            enrolled = async_to_sync(load_or_enroll_charger)(
                "charger-new", ("charger-new", "enroll")
            )
            rejected = async_to_sync(load_or_enroll_charger)(
                "charger-rejected", ("charger-rejected", "wrong")
            )

        self.assertIsNotNone(enrolled)
        self.assertEqual(enrolled.identity, "charger-new")
        self.assertIsNotNone(enrolled.enrolled_at)
        self.assertTrue(enrolled.active)
        self.assertEqual(enrolled.authorization_mode, Charger.AuthorizationMode.OPEN)
        self.assertFalse(Charger.objects.filter(identity="charger-rejected").exists())

    def test_enrollment_is_disabled_without_a_configured_credential(self) -> None:
        with override_settings(OCPP_ENROLLMENT_TOKEN_HASH=""):
            charger = async_to_sync(load_or_enroll_charger)(
                "charger-new", ("charger-new", "enroll")
            )

        self.assertIsNone(charger)
        self.assertFalse(Charger.objects.filter(identity="charger-new").exists())


class AuthorizationPolicyTests(TestCase):
    def setUp(self) -> None:
        self.open_charger = charger("charger-open")
        self.restricted_charger = charger(
            "charger-restricted",
            authorization_mode=Charger.AuthorizationMode.RESTRICTED,
        )
        self.card = CardCredential.objects.create(
            external_id="card-1",
            ocpp_id_tag="known-card",
        )

    def test_open_policy_accepts_unknown_cards_and_records_the_decision(self) -> None:
        result = authorize_id_tag(charger=self.open_charger, id_tag="guest-card")

        self.assertTrue(result.accepted)
        self.assertIsNone(result.card)
        attempt = AuthorizationAttempt.objects.get()
        self.assertTrue(attempt.accepted)
        self.assertEqual(attempt.reason, "open_policy")

    def test_restricted_policy_accepts_only_active_matching_credentials(self) -> None:
        rejected = authorize_id_tag(
            charger=self.restricted_charger,
            id_tag="guest-card",
        )
        accepted = authorize_id_tag(
            charger=self.restricted_charger,
            id_tag=self.card.ocpp_id_tag,
        )

        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.reason, "unknown_or_inactive_credential")
        self.assertTrue(accepted.accepted)
        self.assertEqual(accepted.card, self.card)

    def test_v16_authorize_and_start_follow_the_configured_policy(self) -> None:
        open_actions = Inbound16Actions(self.open_charger)._handlers
        restricted_actions = Inbound16Actions(self.restricted_charger)._handlers

        self.assertEqual(
            async_to_sync(open_actions["Authorize"])({"idTag": "guest-card"}),
            {"idTagInfo": {"status": "Accepted"}},
        )
        started = async_to_sync(open_actions["StartTransaction"])(
            {"connectorId": 1, "idTag": "guest-card"}
        )
        self.assertEqual(started["idTagInfo"], {"status": "Accepted"})
        self.assertEqual(
            async_to_sync(restricted_actions["Authorize"])({"idTag": "guest-card"}),
            {"idTagInfo": {"status": "Invalid"}},
        )
        self.assertEqual(
            async_to_sync(restricted_actions["StartTransaction"])(
                {"connectorId": 1, "idTag": "guest-card"}
            ),
            {"idTagInfo": {"status": "Invalid"}},
        )

    def test_v201_authorize_and_started_transaction_follow_the_policy(self) -> None:
        open_actions = Inbound201Actions(self.open_charger)._handlers
        restricted_actions = Inbound201Actions(self.restricted_charger)._handlers
        payload = {
            "eventType": "Started",
            "idToken": {"idToken": "guest-card"},
            "transactionInfo": {"transactionId": "transaction-open"},
        }

        self.assertEqual(
            async_to_sync(open_actions["Authorize"])(
                {"idToken": {"idToken": "guest-card"}}
            ),
            {"idTokenInfo": {"status": "Accepted"}},
        )
        self.assertEqual(
            async_to_sync(open_actions["TransactionEvent"])(payload),
            {"idTokenInfo": {"status": "Accepted"}},
        )
        self.assertTrue(
            OcppTransaction.objects.filter(
                charger=self.open_charger,
                remote_id="transaction-open",
            ).exists()
        )
        self.assertEqual(
            async_to_sync(restricted_actions["Authorize"])(
                {"idToken": {"idToken": "guest-card"}}
            ),
            {"idTokenInfo": {"status": "Invalid"}},
        )
        self.assertEqual(
            async_to_sync(restricted_actions["TransactionEvent"])(
                {
                    **payload,
                    "transactionInfo": {"transactionId": "transaction-restricted"},
                }
            ),
            {"idTokenInfo": {"status": "Invalid"}},
        )
        self.assertFalse(
            OcppTransaction.objects.filter(
                charger=self.restricted_charger,
                remote_id="transaction-restricted",
            ).exists()
        )

    def test_v201_started_transaction_requires_a_nonempty_identifier(self) -> None:
        actions = Inbound201Actions(self.open_charger)._handlers

        with self.assertRaises(ValueError):
            async_to_sync(actions["TransactionEvent"])(
                {
                    "eventType": "Started",
                    "idToken": {"idToken": ""},
                    "transactionInfo": {"transactionId": "transaction-empty"},
                }
            )

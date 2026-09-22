from unittest.mock import patch

from django.test import TestCase

from apps.cards.models import AuthorizationAttempt, CardCredential
from apps.energy.models import CustomerAccount
from apps.events.models import EventEnvelope
from apps.ocpp.models import Charger
from apps.ocpp.services.authorization import authorize_id_tag
from tests.apps.ocpp.builders import charger


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


class AuthorizationEventTests(TestCase):
    def test_authorization_records_attempt_and_event(self) -> None:
        account = CustomerAccount.objects.create(
            key="account-1",
            name="Account",
            ocpp_id_tag="account-tag",
        )
        card = CardCredential.objects.create(
            external_id="event-card",
            account=account,
            ocpp_id_tag="card-tag",
        )
        selected = charger("charger-event")

        result = authorize_id_tag(charger=selected, id_tag=card.ocpp_id_tag)

        self.assertTrue(result.accepted)
        self.assertEqual(result.account, account)
        self.assertTrue(AuthorizationAttempt.objects.get(presented_id="card-tag").accepted)
        self.assertEqual(
            EventEnvelope.objects.get(event_type="ocpp.authorization").producer,
            "ocpp",
        )

    @patch(
        "apps.events.tasks.process_event.delay",
        side_effect=ConnectionError("broker unavailable"),
    )
    def test_authorization_hot_path_does_not_require_event_broker(self, delay) -> None:
        selected = charger("charger-broker-independent")

        result = authorize_id_tag(charger=selected, id_tag="guest-card")

        self.assertTrue(result.accepted)
        self.assertEqual(
            EventEnvelope.objects.get(event_type="ocpp.authorization").delivery_status,
            EventEnvelope.DeliveryStatus.PENDING,
        )
        delay.assert_not_called()

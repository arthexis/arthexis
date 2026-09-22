from django.test import TestCase

from apps.cards.models import AuthorizationAttempt, CardCredential
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

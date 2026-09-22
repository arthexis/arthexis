from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.cards.models import AuthorizationAttempt, CardCredential
from apps.energy.models import CustomerAccount


class CardCredentialTests(TestCase):
    def test_string_uses_label_then_external_id(self) -> None:
        labelled = CardCredential.objects.create(
            external_id="card-labelled",
            label="Front Desk",
        )
        unlabelled = CardCredential.objects.create(
            external_id="card-unlabelled",
        )

        self.assertEqual(str(labelled), "Front Desk")
        self.assertEqual(str(unlabelled), "card-unlabelled")

    def test_credentials_are_ordered_by_external_id(self) -> None:
        CardCredential.objects.create(external_id="card-z")
        CardCredential.objects.create(external_id="card-a")

        self.assertEqual(
            list(CardCredential.objects.values_list("external_id", flat=True)),
            ["card-a", "card-z"],
        )

    def test_external_id_is_unique(self) -> None:
        CardCredential.objects.create(external_id="duplicate")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CardCredential.objects.create(external_id="duplicate")

    def test_optional_user_and_account_links_are_retained_when_present(self) -> None:
        user = get_user_model().objects.create_user(
            username="card-owner",
            password="unused",
        )
        account = CustomerAccount.objects.create(
            key="account-1",
            name="Account",
        )

        credential = CardCredential.objects.create(
            external_id="card-1",
            user=user,
            account=account,
        )

        self.assertEqual(credential.user, user)
        self.assertEqual(credential.account, account)
        self.assertEqual(user.card_credentials.get(), credential)
        self.assertEqual(account.card_credentials.get(), credential)

    def test_user_and_account_deletion_leave_the_credential(self) -> None:
        user = get_user_model().objects.create_user(
            username="card-owner",
            password="unused",
        )
        account = CustomerAccount.objects.create(
            key="account-1",
            name="Account",
        )
        credential = CardCredential.objects.create(
            external_id="card-1",
            user=user,
            account=account,
        )

        user.delete()
        account.delete()

        credential.refresh_from_db()
        self.assertIsNone(credential.user)
        self.assertIsNone(credential.account)

    def test_defaults_describe_an_active_unassigned_credential(self) -> None:
        credential = CardCredential.objects.create(external_id="card-1")

        self.assertTrue(credential.active)
        self.assertEqual(credential.label, "")
        self.assertEqual(credential.ocpp_id_tag, "")
        self.assertIsNone(credential.user)
        self.assertIsNone(credential.account)


class AuthorizationAttemptTests(TestCase):
    def test_attempt_records_decision_details(self) -> None:
        credential = CardCredential.objects.create(external_id="card-1")

        attempt = AuthorizationAttempt.objects.create(
            card=credential,
            presented_id="presented-card",
            accepted=True,
            reason="matched_credential",
        )

        self.assertEqual(attempt.card, credential)
        self.assertEqual(attempt.presented_id, "presented-card")
        self.assertTrue(attempt.accepted)
        self.assertEqual(attempt.reason, "matched_credential")
        self.assertIsNotNone(attempt.occurred_at)

    def test_attempt_survives_credential_deletion(self) -> None:
        credential = CardCredential.objects.create(external_id="card-1")
        attempt = AuthorizationAttempt.objects.create(
            card=credential,
            presented_id="card-1",
        )

        credential.delete()

        attempt.refresh_from_db()
        self.assertIsNone(attempt.card)
        self.assertEqual(attempt.presented_id, "card-1")

    def test_attempt_defaults_record_a_rejected_unexplained_decision(self) -> None:
        attempt = AuthorizationAttempt.objects.create(presented_id="unknown")

        self.assertFalse(attempt.accepted)
        self.assertEqual(attempt.reason, "")
        self.assertIsNone(attempt.card)

    def test_attempts_are_ordered_newest_first(self) -> None:
        older = AuthorizationAttempt.objects.create(presented_id="older")
        newer = AuthorizationAttempt.objects.create(presented_id="newer")
        AuthorizationAttempt.objects.filter(pk=older.pk).update(
            occurred_at=timezone.now() - timedelta(hours=1)
        )

        self.assertEqual(
            list(
                AuthorizationAttempt.objects.values_list(
                    "presented_id",
                    flat=True,
                )
            ),
            ["newer", "older"],
        )

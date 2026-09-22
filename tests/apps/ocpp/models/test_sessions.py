from datetime import datetime, timezone

from django.test import TestCase

from apps.ocpp.domain.sessions import (
    current_transaction,
    last_completed_transaction,
    last_transaction,
)
from apps.ocpp.models import OcppTransaction
from tests.apps.ocpp.builders import charger, transaction


class OcppTransactionTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("charger-1")

    def test_transaction_queries_and_read_helpers_are_deterministic(self) -> None:
        first = transaction(
            self.charger,
            "first",
            started_at=datetime(2026, 9, 19, 10, tzinfo=timezone.utc),
            stopped_at=datetime(2026, 9, 19, 11, tzinfo=timezone.utc),
        )
        active_older = transaction(
            self.charger,
            "active-older",
            started_at=datetime(2026, 9, 19, 12, tzinfo=timezone.utc),
        )
        active_newer = transaction(
            self.charger,
            "active-newer",
            started_at=datetime(2026, 9, 19, 13, tzinfo=timezone.utc),
        )
        completed_newer = transaction(
            self.charger,
            "completed-newer",
            started_at=datetime(2026, 9, 19, 14, tzinfo=timezone.utc),
            stopped_at=datetime(2026, 9, 19, 15, tzinfo=timezone.utc),
        )

        self.assertQuerySetEqual(
            OcppTransaction.objects.active().recent(),
            [active_newer, active_older],
        )
        self.assertQuerySetEqual(
            OcppTransaction.objects.completed().recent(),
            [completed_newer, first],
        )
        self.assertEqual(current_transaction(self.charger), active_newer)
        self.assertEqual(last_transaction(self.charger), completed_newer)
        self.assertEqual(last_completed_transaction(self.charger), completed_newer)

    def test_transaction_recency_uses_primary_key_to_break_timestamp_ties(
        self,
    ) -> None:
        timestamp = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
        older_pk = transaction(self.charger, "tie-1", started_at=timestamp)
        newer_pk = transaction(self.charger, "tie-2", started_at=timestamp)

        self.assertEqual(list(OcppTransaction.objects.recent()), [newer_pk, older_pk])
        self.assertEqual(current_transaction(self.charger), newer_pk)

    def test_transaction_read_helpers_return_none_without_transactions(self) -> None:
        self.assertIsNone(current_transaction(self.charger))
        self.assertIsNone(last_transaction(self.charger))
        self.assertIsNone(last_completed_transaction(self.charger))

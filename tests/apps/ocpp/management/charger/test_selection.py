from datetime import datetime, timezone

from django.core.management.base import CommandError
from django.test import TestCase

from apps.ocpp.management.charger.selection import select_chargers
from tests.apps.ocpp.builders import charger, connection, transaction


class ChargerSelectionTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("charger-1")
        connection(self.charger, channel_name="fleet-channel")
        transaction(
            self.charger,
            "transaction-active",
            started_at=datetime(2026, 1, 1, 2, tzinfo=timezone.utc),
        )

    def test_rejects_unknown_duplicate_and_mixed_targets(self) -> None:
        with self.assertRaisesMessage(CommandError, "Unknown charger"):
            list(
                select_chargers(
                    identities=["missing"],
                    select_all=False,
                )
            )
        with self.assertRaisesMessage(CommandError, "only once"):
            list(
                select_chargers(
                    identities=[self.charger.identity, self.charger.identity],
                    select_all=False,
                )
            )
        with self.assertRaisesMessage(CommandError, "--all by itself"):
            list(
                select_chargers(
                    identities=[self.charger.identity],
                    select_all=True,
                )
            )

    def test_filters_compose_across_dimensions(self) -> None:
        idle = charger("charger-idle")
        connection(idle, channel_name="idle-channel")
        disabled = charger("charger-disabled", active=False)
        unresolved = charger("charger-unresolved")
        connection(unresolved, channel_name="unresolved-channel")
        unresolved_transaction = transaction(
            unresolved,
            "transaction-unresolved",
            started_at=datetime(2026, 1, 1, 3, tzinfo=timezone.utc),
        )
        unresolved_transaction.recovery_state = (
            unresolved_transaction.RecoveryState.UNRESOLVED
        )
        unresolved_transaction.save(update_fields=("recovery_state",))

        enabled_connected = list(
            select_chargers(
                identities=[],
                select_all=False,
                filters=("enabled", "connected"),
            )
        )
        self.assertIn(self.charger, enabled_connected)
        self.assertIn(idle, enabled_connected)
        self.assertNotIn(disabled, enabled_connected)

        idle_only = list(
            select_chargers(
                identities=[],
                select_all=False,
                filters=("idle",),
            )
        )
        self.assertIn(idle, idle_only)
        self.assertNotIn(self.charger, idle_only)
        self.assertNotIn(unresolved, idle_only)

        unresolved_only = list(
            select_chargers(
                identities=[],
                select_all=False,
                filters=("unresolved",),
            )
        )
        self.assertEqual(unresolved_only, [unresolved])

    def test_rejects_unknown_filter_names(self) -> None:
        with self.assertRaisesMessage(CommandError, "Unknown fleet filter"):
            list(
                select_chargers(
                    identities=[],
                    select_all=False,
                    filters=("reset",),
                )
            )

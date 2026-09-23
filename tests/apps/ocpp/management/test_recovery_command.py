import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.ocpp.models import OcppTransaction
from tests.apps.ocpp.builders import charger, connection, connector, transaction


class OcppRecoveryCommandTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("field-recovery")
        connection(self.charger, channel_name="field-recovery-channel")
        connector(self.charger, number=1, status="Charging")
        self.session = transaction(
            self.charger,
            "stale-session",
        )

    def test_inspection_is_charger_centric_and_read_only(self) -> None:
        output = StringIO()

        call_command(
            "ocpp_recovery",
            charger=self.charger.identity,
            stdout=output,
        )

        text = output.getvalue()
        self.assertIn("Charger: field-recovery", text)
        self.assertIn("State: charging", text)
        self.assertIn("Connectors: 1:Charging", text)
        self.assertIn("Current session: stale-session", text)
        self.assertIn("Why: active session stale-session", text)
        self.assertIn(
            "Waiting for: transaction end or newer charger evidence",
            text,
        )
        self.assertIn("Last seen:", text)
        self.assertIn("Presence lease expires:", text)
        self.assertIn("Session last evidence:", text)

        self.session.refresh_from_db()
        self.assertEqual(
            self.session.recovery_state,
            OcppTransaction.RecoveryState.ACTIVE,
        )

    def test_clear_stale_state_restores_idle_without_selecting_transaction(self) -> None:
        output = StringIO()

        call_command(
            "ocpp_recovery",
            charger=self.charger.identity,
            clear_stale_state=True,
            reason="verified idle at charger",
            stdout=output,
        )

        text = output.getvalue()
        self.assertIn("State: idle", text)
        self.assertIn("Connectors: 1:Charging", text)
        self.assertIn("Operator-cleared sessions: 1", text)
        self.assertIn("Last cleared session: stale-session", text)
        self.assertIn("Reason: verified idle at charger", text)
        self.assertIn("Cleared 1 stale live session(s)", text)

        self.session.refresh_from_db()
        self.assertEqual(
            self.session.recovery_state,
            OcppTransaction.RecoveryState.CLEARED,
        )
        self.assertIsNone(self.session.stopped_at)

    def test_clear_stale_state_is_safe_when_nothing_is_open(self) -> None:
        self.session.delete()
        output = StringIO()

        call_command(
            "ocpp_recovery",
            charger=self.charger.identity,
            clear_stale_state=True,
            stdout=output,
        )

        self.assertIn("Cleared 0 stale live session(s)", output.getvalue())

    def test_json_output_exposes_recovery_context(self) -> None:
        output = StringIO()

        call_command(
            "ocpp_recovery",
            charger=self.charger.identity,
            clear_stale_state=True,
            reason="field reset",
            json_output=True,
            stdout=output,
        )

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["identity"], "field-recovery")
        self.assertEqual(payload["state"], "idle")
        self.assertEqual(payload["cleared_sessions"], 1)
        self.assertEqual(payload["last_cleared_transaction_id"], "stale-session")
        self.assertEqual(payload["last_recovery_clear_reason"], "field reset")
        self.assertEqual(payload["cleared_session_count"], 1)
        self.assertEqual(
            payload["state_reason"],
            "live charger presence with no active or unresolved session",
        )
        self.assertIsNone(payload["waiting_for"])
        self.assertIsNotNone(payload["connection_last_seen_at"])
        self.assertIsNotNone(payload["connection_lease_expires_at"])

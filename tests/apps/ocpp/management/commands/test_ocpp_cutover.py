from datetime import datetime, timezone
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from tests.apps.ocpp.builders import charger


class OcppCutoverCommandTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("legacy-charger")

    def test_show_reports_unset_cutover_without_mutation(self) -> None:
        output = StringIO()

        call_command("ocpp_cutover", self.charger.identity, stdout=output)

        self.charger.refresh_from_db()
        self.assertIsNone(self.charger.authority_cutover_at)
        self.assertEqual(
            output.getvalue().strip(),
            "legacy-charger: authority cutover unset",
        )

    def test_set_persists_explicit_offset_aware_cutover(self) -> None:
        output = StringIO()

        call_command(
            "ocpp_cutover",
            self.charger.identity,
            "--set",
            "2026-09-21T14:30:00-06:00",
            stdout=output,
        )

        self.charger.refresh_from_db()
        self.assertEqual(
            self.charger.authority_cutover_at,
            datetime(2026, 9, 21, 20, 30, tzinfo=timezone.utc),
        )
        self.assertIn("authority cutover", output.getvalue())

    def test_set_accepts_z_suffix(self) -> None:
        call_command(
            "ocpp_cutover",
            self.charger.identity,
            "--set",
            "2026-09-22T12:00:00Z",
            stdout=StringIO(),
        )

        self.charger.refresh_from_db()
        self.assertEqual(
            self.charger.authority_cutover_at,
            datetime(2026, 9, 22, 12, tzinfo=timezone.utc),
        )

    def test_set_rejects_naive_timestamp(self) -> None:
        with self.assertRaisesMessage(
            CommandError,
            "Cutover must include a timezone offset or Z suffix",
        ):
            call_command(
                "ocpp_cutover",
                self.charger.identity,
                "--set",
                "2026-09-22T12:00:00",
            )

        self.charger.refresh_from_db()
        self.assertIsNone(self.charger.authority_cutover_at)

    def test_set_rejects_invalid_timestamp(self) -> None:
        with self.assertRaisesMessage(
            CommandError,
            "Cutover must be a valid ISO-8601 timestamp",
        ):
            call_command(
                "ocpp_cutover",
                self.charger.identity,
                "--set",
                "yesterday",
            )

    def test_clear_removes_existing_cutover(self) -> None:
        self.charger.authority_cutover_at = datetime(
            2026, 9, 22, 12, tzinfo=timezone.utc
        )
        self.charger.save(update_fields=("authority_cutover_at",))
        output = StringIO()

        call_command(
            "ocpp_cutover",
            self.charger.identity,
            "--clear",
            stdout=output,
        )

        self.charger.refresh_from_db()
        self.assertIsNone(self.charger.authority_cutover_at)
        self.assertEqual(
            output.getvalue().strip(),
            "legacy-charger: authority cutover unset",
        )

    def test_unknown_charger_is_rejected(self) -> None:
        with self.assertRaisesMessage(CommandError, "Unknown charger: missing"):
            call_command("ocpp_cutover", "missing")

    def test_set_and_clear_are_mutually_exclusive(self) -> None:
        with self.assertRaisesMessage(CommandError, "not allowed with argument"):
            call_command(
                "ocpp_cutover",
                self.charger.identity,
                "--set",
                "2026-09-22T12:00:00Z",
                "--clear",
            )

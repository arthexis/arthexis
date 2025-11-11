import io
from datetime import timedelta

from django.core.management import call_command, CommandError
from django.test import TestCase
from django.utils import timezone

from core.models import CustomerAccount
from ocpp.models import Charger, RFIDSessionAttempt


class RFIDHistoryCommandTests(TestCase):
    def setUp(self):
        self.charger = Charger.objects.create(charger_id="CMDTEST")
        self.account = CustomerAccount.objects.create(name="Primary")

    def test_no_attempts_reports_placeholder(self):
        out = io.StringIO()
        call_command("rfid_history", stdout=out)
        self.assertIn("No RFID session attempts recorded.", out.getvalue())

    def test_shows_recent_attempts(self):
        older = RFIDSessionAttempt.objects.create(
            charger=self.charger,
            rfid="OLDTAG",
            status=RFIDSessionAttempt.Status.ACCEPTED,
            account=self.account,
        )
        middle = RFIDSessionAttempt.objects.create(
            charger=self.charger,
            rfid="MIDTAG",
            status=RFIDSessionAttempt.Status.REJECTED,
        )
        newest = RFIDSessionAttempt.objects.create(
            charger=self.charger,
            rfid="NEWEST",
            status=RFIDSessionAttempt.Status.ACCEPTED,
        )

        timestamps = [
            timezone.now() - timedelta(minutes=10),
            timezone.now() - timedelta(minutes=5),
            timezone.now() - timedelta(minutes=1),
        ]
        for attempt, ts in zip((older, middle, newest), timestamps, strict=True):
            RFIDSessionAttempt.objects.filter(pk=attempt.pk).update(attempted_at=ts)

        out = io.StringIO()
        call_command("rfid_history", "--last", "3", stdout=out)
        lines = [line for line in out.getvalue().splitlines() if line.strip()]
        self.assertGreaterEqual(len(lines), 4)
        header, first_row, second_row, third_row = (
            lines[0],
            lines[1],
            lines[2],
            lines[3],
        )
        self.assertIn("RFID", header)
        self.assertIn("Status", header)
        self.assertIn("Account", header)
        self.assertIn("NEWEST", first_row)
        self.assertIn("Accepted", first_row)
        self.assertTrue(first_row.rstrip().endswith("-"))
        self.assertIn("MIDTAG", second_row)
        self.assertIn("Rejected", second_row)
        self.assertTrue(second_row.rstrip().endswith("-"))
        self.assertIn("OLDTAG", third_row)
        self.assertIn(self.account.name.upper(), third_row)

    def test_invalid_last_raises(self):
        with self.assertRaises(CommandError):
            call_command("rfid_history", "--last", "0")

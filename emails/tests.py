from django.test import TestCase
from unittest.mock import patch

from .models import EmailPattern
from .tasks import send_pending_emails


class EmailPatternMatchTests(TestCase):
    def test_matches_extracts_variables(self):
        pattern = EmailPattern(
            name="invoice",
            subject="Invoice [number]",
            body="Total: [amount]",
        )
        message = {
            "from_address": "",
            "to_address": "",
            "cc": "",
            "bcc": "",
            "subject": "Your Invoice 123",
            "body": "Hello\nTotal: 456.78\nThanks",
        }
        self.assertEqual(
            pattern.matches(message), {"number": "123", "amount": "456.78"}
        )

    def test_no_match_returns_empty_dict(self):
        pattern = EmailPattern(name="greeting", subject="Hello [name]")
        message = {"subject": "No greeting"}
        self.assertEqual(pattern.matches(message), {})


class SendPendingEmailsTaskTests(TestCase):
    def test_task_calls_send_queued_mail_until_done(self):
        with patch("emails.tasks.send_queued_mail_until_done") as mock_send:
            send_pending_emails.run()
            mock_send.assert_called_once_with()

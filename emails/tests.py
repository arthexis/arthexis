from django.test import TestCase

from .models import EmailPattern


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

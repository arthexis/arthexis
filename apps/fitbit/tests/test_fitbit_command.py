"""Tests for Fitbit management command workflows."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.fitbit.models import FitbitConnection, FitbitHealthSample, FitbitNetMessageDelivery


class FitbitCommandTests(TestCase):
    """Validate the fitbit CLI command for setup, storage, and net-message routing."""

    def test_configure_and_store_query_payload(self):
        """The command should create a connection and persist an inline JSON sample."""
        call_command(
            "fitbit",
            "configure",
            "watch-band",
            "--user-id",
            "fitbit-user",
            "--access-token",
            "token-1",
        )

        call_command(
            "fitbit",
            "query",
            "watch-band",
            "--resource",
            "steps",
            "--json",
            '{"steps": 7421}',
        )

        self.assertTrue(FitbitConnection.objects.filter(name="watch-band").exists())
        sample = FitbitHealthSample.objects.get(connection__name="watch-band")
        self.assertEqual(sample.resource, "steps")
        self.assertEqual(sample.payload["steps"], 7421)

    def test_net_test_creates_delivery(self):
        """The net-test command should create a Fitbit delivery for fitbit channel messages."""
        FitbitConnection.objects.create(
            name="runner",
            fitbit_user_id="fitbit-runner",
            access_token="token-x",
        )

        call_command("fitbit", "net-test", "runner", "--subject", "Hi", "--body", "Body")

        delivery = FitbitNetMessageDelivery.objects.get(connection__name="runner")
        self.assertEqual(delivery.net_message.lcd_channel_type, "fitbit")

    def test_query_list_outputs_rows(self):
        """The query --list action should render stored sample lines to stdout."""
        connection = FitbitConnection.objects.create(
            name="walker",
            fitbit_user_id="fitbit-walker",
            access_token="token-w",
        )
        FitbitHealthSample.objects.create(
            connection=connection,
            resource="heart",
            payload={"bpm": 62},
        )

        out = StringIO()
        call_command("fitbit", "query", "walker", "--list", stdout=out)

        self.assertIn("resource=heart", out.getvalue())

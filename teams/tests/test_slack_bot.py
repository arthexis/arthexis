import hashlib
import hmac
import time
import urllib.parse
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from nodes.models import NetMessage, Node

from teams.models import SlackBotProfile


class SlackBotProfileTests(TestCase):
    def setUp(self):
        self.mac = "00:11:22:33:44:55"
        self.mac_patcher = mock.patch(
            "nodes.models.Node.get_current_mac", return_value=self.mac
        )
        self.mac_patcher.start()
        self.addCleanup(self.mac_patcher.stop)
        self.node = Node.objects.create(
            hostname="local", port=8888, mac_address=self.mac, current_relation=Node.Relation.SELF
        )
        self.bot = SlackBotProfile.objects.create(
            node=self.node,
            team_id="T12345",
            bot_token="xoxb-test-token",
            signing_secret="test-secret",
            default_channels=["C123", "C456"],
        )

    def test_slack_bot_requires_owner(self):
        profile = SlackBotProfile(
            team_id="T999",
            bot_token="xoxb-token",
            signing_secret="signing",
            default_channels=["C001"],
        )
        with self.assertRaises(ValidationError):
            profile.full_clean()

    @mock.patch("teams.models.requests.post")
    def test_broadcast_posts_to_all_channels(self, mock_post):
        mock_response = mock.Mock(status_code=200, ok=True)
        mock_response.json.return_value = {"ok": True}
        mock_post.return_value = mock_response

        NetMessage.broadcast("Status", "Hello from Slack bot")

        self.assertEqual(mock_post.call_count, len(self.bot.get_channels()))
        for call in mock_post.call_args_list:
            url = call.args[0]
            self.assertIn("chat.postMessage", url)
            payload = call.kwargs.get("json")
            self.assertIsInstance(payload, dict)
            self.assertIn(payload["channel"], self.bot.get_channels())

    @mock.patch("teams.models.requests.post")
    def test_slack_command_broadcasts_net_message(self, mock_post):
        mock_response = mock.Mock(status_code=200, ok=True)
        mock_response.json.return_value = {"ok": True}
        mock_post.return_value = mock_response

        url = reverse("teams:slack-command")
        timestamp = str(int(time.time()))
        payload = {
            "team_id": "T12345",
            "user_name": "Commander",
            "user_id": "U999",
            "channel_name": "ops",
            "channel_id": "C111",
            "command": "/art",
            "text": "net Alert | Propagate to network",
        }
        body = urllib.parse.urlencode(payload)
        signature = self._sign(body, timestamp, self.bot.get_signing_secret())

        response = self.client.post(
            url,
            data=body,
            content_type="application/x-www-form-urlencoded",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_SLACK_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("Net Message broadcast", data["text"])

        message = NetMessage.objects.latest("pk")
        self.assertEqual(message.subject, "Alert")
        self.assertIn("Sent from Slack", message.body)
        self.assertEqual(mock_post.call_count, len(self.bot.get_channels()))

    def test_slack_command_rejects_invalid_signature(self):
        url = reverse("teams:slack-command")
        payload = {"team_id": "T12345", "command": "/art", "text": "net hi"}
        body = urllib.parse.urlencode(payload)

        initial_count = NetMessage.objects.count()

        response = self.client.post(
            url,
            data=body,
            content_type="application/x-www-form-urlencoded",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=str(int(time.time())),
            HTTP_X_SLACK_SIGNATURE="v0=invalid",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(NetMessage.objects.count(), initial_count)

    def test_slack_command_requires_local_node(self):
        other_node = Node.objects.create(
            hostname="remote",
            port=9000,
            mac_address="66:55:44:33:22:11",
            current_relation=Node.Relation.PEER,
        )
        self.bot.node = other_node
        self.bot.save(update_fields=["node"])

        url = reverse("teams:slack-command")
        timestamp = str(int(time.time()))
        payload = {"team_id": "T12345", "command": "/art", "text": "net hi"}
        body = urllib.parse.urlencode(payload)
        signature = self._sign(body, timestamp, self.bot.get_signing_secret())

        initial_count = NetMessage.objects.count()

        response = self.client.post(
            url,
            data=body,
            content_type="application/x-www-form-urlencoded",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_SLACK_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(NetMessage.objects.count(), initial_count)

    @staticmethod
    def _sign(body: str, timestamp: str, secret: str) -> str:
        payload = f"v0:{timestamp}:{body}".encode("utf-8")
        digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256)
        return f"v0={digest.hexdigest()}"

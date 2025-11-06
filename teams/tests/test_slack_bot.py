import hashlib
import hmac
import time
import urllib.parse
from unittest import mock

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from nodes.models import NetMessage, Node

from teams.admin import SlackBotProfileAdmin
from teams.forms import SlackBotProfileAdminForm
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


class SlackBotProfileAdminTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.site = AdminSite()
        self.factory = RequestFactory()
        self.node = Node.objects.create(
            hostname="local",
            port=8443,
            mac_address="AA:BB:CC:DD:EE:FF",
            current_relation=Node.Relation.SELF,
        )

    @mock.patch("nodes.models.Node.get_local")
    def test_initial_node_defaults_to_local(self, mock_get_local):
        mock_get_local.return_value = self.node
        request = self.factory.get("/admin/teams/slackbotprofile/add/")
        request.user = self.user

        admin = SlackBotProfileAdmin(SlackBotProfile, self.site)
        initial = admin.get_changeform_initial_data(request)

        self.assertEqual(initial.get("node"), self.node.pk)
        mock_get_local.assert_called_once_with()

    def test_admin_form_sets_help_text_and_placeholders(self):
        form = SlackBotProfileAdminForm()

        self.assertIn("Defaults to the current node", form.fields["node"].help_text)
        self.assertIn("Optional", form.fields["user"].help_text)
        self.assertTrue(form.fields["team_id"].widget.attrs.get("placeholder").startswith("T"))
        self.assertTrue(
            form.fields["bot_token"].widget.attrs.get("placeholder", "").startswith("xoxb-")
        )

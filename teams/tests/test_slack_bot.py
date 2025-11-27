import hashlib
import hmac
import time
import urllib.parse
from unittest import mock

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings
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

    def test_admin_uses_raw_id_fields_for_related_owners(self):
        admin = SlackBotProfileAdmin(SlackBotProfile, self.site)

        self.assertEqual(admin.raw_id_fields, ("node", "user", "group"))


class SlackBotProfileWizardTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )
        self.client.force_login(self.user)
        self.node = Node.objects.create(
            hostname="local",
            port=8443,
            mac_address="AA:BB:CC:DD:EE:FF",
            current_relation=Node.Relation.SELF,
        )

    @override_settings(
        SLACK_CLIENT_ID="client",
        SLACK_CLIENT_SECRET="secret",
        SLACK_SIGNING_SECRET="signing",
        SLACK_BOT_SCOPES="commands,chat:write",
    )
    def test_wizard_redirects_to_slack_authorize(self):
        response = self.client.get(
            reverse("admin:teams_slackbotprofile_bot_creation_wizard")
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("slack.com/oauth/v2/authorize", response["Location"])
        self.assertIn("client_id=client", response["Location"])
        self.assertTrue(self.client.session.get("slack_bot_wizard_state"))

    @override_settings(
        SLACK_CLIENT_ID="client",
        SLACK_CLIENT_SECRET="secret",
        SLACK_SIGNING_SECRET="signing",
        SLACK_BOT_SCOPES="commands,chat:write",
        SLACK_REDIRECT_URL="https://example.com/slack/callback/",
    )
    def test_wizard_uses_configured_redirect_url(self):
        response = self.client.get(
            reverse("admin:teams_slackbotprofile_bot_creation_wizard")
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            "redirect_uri=https%3A%2F%2Fexample.com%2Fslack%2Fcallback%2F",
            response["Location"],
        )

    @override_settings(
        SLACK_CLIENT_ID="client",
        SLACK_CLIENT_SECRET="secret",
        SLACK_SIGNING_SECRET="signing",
        SLACK_BOT_SCOPES="commands,chat:write",
        ALLOWED_HOSTS=["testserver", "10.0.0.1"],
    )
    def test_wizard_requires_domain_when_host_is_ip(self):
        response = self.client.get(
            reverse("admin:teams_slackbotprofile_bot_creation_wizard"),
            HTTP_HOST="10.0.0.1:8888",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "domain-based callback URL")
        self.assertContains(response, "Continue to Slack")
        self.assertContains(response, "disabled")

    @override_settings(
        SLACK_CLIENT_ID="client",
        SLACK_CLIENT_SECRET="secret",
        SLACK_SIGNING_SECRET="signing",
        SLACK_BOT_SCOPES="commands,chat:write",
        ALLOWED_HOSTS=["testserver", "10.0.0.1", "node.example.com"],
    )
    @mock.patch("teams.admin.Node.get_local")
    def test_wizard_prefers_network_hostname_over_ip_host(self, mock_get_local):
        self.node.network_hostname = "node.example.com"
        self.node.save(update_fields=["network_hostname"])
        mock_get_local.return_value = self.node

        response = self.client.get(
            reverse("admin:teams_slackbotprofile_bot_creation_wizard"),
            HTTP_HOST="10.0.0.1:8888",
        )

        self.assertEqual(response.status_code, 302)
        location = urllib.parse.unquote(response["Location"])
        self.assertIn("node.example.com:8888", location)

    @override_settings(
        SLACK_CLIENT_ID="client",
        SLACK_CLIENT_SECRET="secret",
        SLACK_SIGNING_SECRET="signing",
        SLACK_BOT_SCOPES="commands,chat:write",
        SLACK_REDIRECT_URL="https://example.com/slack/callback/",
    )
    @mock.patch("teams.admin.requests.post")
    @mock.patch("teams.admin.Node.get_local")
    def test_callback_creates_bot_profile(self, mock_get_local, mock_post):
        mock_get_local.return_value = self.node
        session = self.client.session
        session["slack_bot_wizard_state"] = "state-token"
        session.save()
        mock_response = mock.Mock()
        mock_response.json.return_value = {
            "ok": True,
            "access_token": "xoxb-new-token",
            "bot_user_id": "B123",
            "team": {"id": "TNEW"},
            "incoming_webhook": {"channel_id": "C123"},
        }
        mock_post.return_value = mock_response

        response = self.client.get(
            reverse("admin:teams_slackbotprofile_bot_creation_callback"),
            {"state": "state-token", "code": "auth-code"},
        )

        profile = SlackBotProfile.objects.get(team_id="TNEW")
        self.assertEqual(profile.bot_token, "xoxb-new-token")
        self.assertEqual(profile.bot_user_id, "B123")
        self.assertEqual(profile.default_channels, ["C123"])
        called_kwargs = mock_post.call_args.kwargs
        self.assertEqual(
            called_kwargs["data"]["redirect_uri"],
            "https://example.com/slack/callback/",
        )
        self.assertRedirects(
            response,
            reverse("admin:teams_slackbotprofile_change", args=[profile.pk]),
        )

    @override_settings(
        SLACK_CLIENT_ID="",
        SLACK_CLIENT_SECRET="",
        SLACK_SIGNING_SECRET="",
    )
    def test_wizard_prompts_for_configuration(self):
        response = self.client.get(
            reverse("admin:teams_slackbotprofile_bot_creation_wizard"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Slack bot wizard")
        self.assertContains(response, "client_id")

    @override_settings(
        SLACK_CLIENT_ID="",
        SLACK_CLIENT_SECRET="",
        SLACK_SIGNING_SECRET="",
    )
    def test_wizard_accepts_manual_configuration(self):
        response = self.client.post(
            reverse("admin:teams_slackbotprofile_bot_creation_wizard"),
            {
                "client_id": "manual-client",
                "client_secret": "manual-secret",
                "signing_secret": "manual-signing",
                "scopes": "commands,chat:write",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("client_id=manual-client", response["Location"])
        session_config = self.client.session.get("slack_bot_wizard_config")
        self.assertEqual(session_config["client_secret"], "manual-secret")
        self.assertTrue(self.client.session.get("slack_bot_wizard_state"))

    @override_settings(
        SLACK_CLIENT_ID="",
        SLACK_CLIENT_SECRET="",
        SLACK_SIGNING_SECRET="",
    )
    @mock.patch("teams.admin.requests.post")
    @mock.patch("teams.admin.Node.get_local")
    def test_callback_uses_session_configuration(self, mock_get_local, mock_post):
        mock_get_local.return_value = self.node
        session = self.client.session
        session["slack_bot_wizard_state"] = "state-token"
        session["slack_bot_wizard_config"] = {
            "client_id": "manual-client",
            "client_secret": "manual-secret",
            "signing_secret": "manual-signing",
            "scopes": "commands",
        }
        session.save()
        mock_response = mock.Mock()
        mock_response.json.return_value = {
            "ok": True,
            "access_token": "xoxb-new-token",
            "bot_user_id": "B123",
            "team": {"id": "TNEW"},
            "incoming_webhook": {"channel_id": "C123"},
        }
        mock_post.return_value = mock_response

        response = self.client.get(
            reverse("admin:teams_slackbotprofile_bot_creation_callback"),
            {"state": "state-token", "code": "auth-code"},
        )

        profile = SlackBotProfile.objects.get(team_id="TNEW")
        self.assertEqual(profile.bot_token, "xoxb-new-token")
        mock_post.assert_called_once()
        called_kwargs = mock_post.call_args.kwargs
        self.assertEqual(called_kwargs["data"]["client_id"], "manual-client")
        self.assertEqual(called_kwargs["data"]["client_secret"], "manual-secret")
        self.assertRedirects(
            response,
            reverse("admin:teams_slackbotprofile_change", args=[profile.pk]),
        )

"""Tests for AssistantProfile admin features."""

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test import RequestFactory, TestCase
from unittest import mock

from core.admin import AssistantProfileAdmin
from core.models import AssistantProfile, hash_key
from core.mcp import process as mcp_process


class AssistantProfileAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="admin", email="a@example.com", password="pwd"
        )
        self.user = User.objects.create_user(username="bob", password="pwd")
        self.profile = AssistantProfile.objects.create(
            user=self.user, user_key_hash="0" * 64
        )
        self.client.force_login(self.admin)

    def test_change_form_has_generate_key_link(self):
        url = reverse(
            "admin:teams_assistantprofile_change",
            args=[self.profile.pk],
        )
        response = self.client.get(url)
        self.assertContains(response, "../generate-key/")

    def test_change_form_shows_gpt_instructions(self):
        url = reverse(
            "admin:teams_assistantprofile_change",
            args=[self.profile.pk],
        )
        response = self.client.get(url)
        self.assertContains(response, "/api/chat/")
        self.assertContains(response, "Authorization")
        self.assertContains(response, "ChatGPT Developer Mode")
        self.assertContains(response, "Assistant Profiles list")
        self.assertContains(response, "Error fetching OAuth configuration")
        self.assertContains(response, "MCP_SIGIL_RESOURCE_URL")

    def test_generate_key_button(self):
        url = reverse(
            "admin:core_assistantprofile_generate_key",
            args=[self.profile.pk],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        context = getattr(response, "context_data", None) or response.context
        key = context["user_key"]
        self.assertTrue(key)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.user_key_hash, hash_key(key))

    def test_change_list_shows_server_controls(self):
        url = reverse("admin:teams_assistantprofile_changelist")
        response = self.client.get(url)
        self.assertContains(response, "Start MCP server")
        self.assertContains(response, "Stop MCP server")
        self.assertContains(response, "Model Context Protocol server")

    @mock.patch.object(mcp_process, "start_server")
    def test_start_server_action_invokes_manager(self, start_server):
        start_server.return_value = 123
        url = reverse("admin:teams_assistantprofile_start_server")
        response = self.client.get(url)
        self.assertRedirects(
            response,
            reverse("admin:teams_assistantprofile_changelist"),
            fetch_redirect_response=False,
        )
        start_server.assert_called_once_with()

    @mock.patch.object(mcp_process, "stop_server")
    def test_stop_server_action_invokes_manager(self, stop_server):
        stop_server.return_value = 123
        url = reverse("admin:teams_assistantprofile_stop_server")
        response = self.client.get(url)
        self.assertRedirects(
            response,
            reverse("admin:teams_assistantprofile_changelist"),
            fetch_redirect_response=False,
        )
        stop_server.assert_called_once_with()

    @mock.patch.object(mcp_process, "get_status")
    def test_status_action_reports_running_state(self, get_status):
        get_status.return_value = {
            "running": True,
            "pid": 42,
            "log_excerpt": "",
            "last_error": "",
        }
        url = reverse("admin:teams_assistantprofile_status")
        response = self.client.get(url)
        self.assertRedirects(
            response,
            reverse("admin:teams_assistantprofile_changelist"),
            fetch_redirect_response=False,
        )
        get_status.assert_called_once_with()


class AssistantProfileAdminActionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="mcpadmin", email="a@example.com", password="pwd"
        )
        self.profile = AssistantProfile.objects.create(user=self.user)
        self.factory = RequestFactory()
        self.admin = AssistantProfileAdmin(AssistantProfile, AdminSite())

    def _get_request(self, user=None):
        request = self.factory.get("/")
        request.user = user or self.user
        request.session = self.client.session
        from django.contrib.messages.storage.fallback import FallbackStorage

        request._messages = FallbackStorage(request)
        return request

    def test_my_profile_redirects_to_existing_profile(self):
        request = self._get_request()
        response = self.admin.my_profile(request, AssistantProfile.objects.none())
        self.assertEqual(response.status_code, 302)
        expected = reverse(
            "admin:core_assistantprofile_change", args=[self.profile.pk]
        )
        self.assertEqual(response.url, expected)

    def test_my_profile_redirects_to_add_when_missing(self):
        self.profile.delete()
        request = self._get_request()
        response = self.admin.my_profile(request, AssistantProfile.objects.none())
        self.assertEqual(response.status_code, 302)
        expected = f"{reverse('admin:core_assistantprofile_add')}?user={self.user.pk}"
        self.assertEqual(response.url, expected)

    def test_my_profile_without_add_permission_shows_error(self):
        self.profile.delete()
        User = get_user_model()
        limited = User.objects.create_user(
            username="limited", password="pwd", is_staff=True
        )
        request = self._get_request(user=limited)
        response = self.admin.my_profile(request, AssistantProfile.objects.none())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("admin:core_assistantprofile_changelist"),
        )
        messages = [m.message.lower() for m in request._messages]
        self.assertTrue(any("permission" in message for message in messages))

    def test_my_profile_change_action_redirects(self):
        request = self._get_request()
        response = self.admin.my_profile_action(request, self.profile)
        self.assertEqual(response.status_code, 302)
        expected = reverse(
            "admin:core_assistantprofile_change", args=[self.profile.pk]
        )
        self.assertEqual(response.url, expected)

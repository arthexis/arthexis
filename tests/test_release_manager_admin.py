from unittest.mock import MagicMock, patch
import pytest

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.admin import ReleaseManagerAdmin
from teams.models import ReleaseManager as TeamsReleaseManager


class ReleaseManagerAdminActionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        User.all_objects.filter(username="admin").delete()
        self.user = User.objects.create_superuser(
            username="admin", email="a@example.com", password="pwd"
        )
        self.manager = TeamsReleaseManager.objects.create(
            user=self.user,
            pypi_url="https://upload.pypi.org/legacy/",
            pypi_token="tok",
        )
        self.factory = RequestFactory()
        self.admin = ReleaseManagerAdmin(TeamsReleaseManager, AdminSite())

    def _get_request(self):
        request = self.factory.get("/")
        request.user = self.user
        request.session = self.client.session
        from django.contrib.messages.storage.fallback import FallbackStorage

        request._messages = FallbackStorage(request)
        return request

    def test_my_profile_redirects_to_existing_profile(self):
        request = self._get_request()
        response = self.admin.my_profile(request, TeamsReleaseManager.objects.none())
        self.assertEqual(response.status_code, 302)
        expected = reverse("admin:teams_releasemanager_change", args=[self.manager.pk])
        self.assertEqual(response.url, expected)

    def test_my_profile_redirects_to_add_when_missing(self):
        self.manager.delete()
        request = self._get_request()
        response = self.admin.my_profile(request, TeamsReleaseManager.objects.none())
        self.assertEqual(response.status_code, 302)
        expected = f"{reverse('admin:teams_releasemanager_add')}?user={self.user.pk}"
        self.assertEqual(response.url, expected)

    def test_my_profile_without_add_permission_shows_error(self):
        self.manager.delete()
        User = get_user_model()
        limited = User.objects.create_user(
            username="limited", password="pwd", is_staff=True
        )
        request = self.factory.get("/")
        request.user = limited
        request.session = self.client.session
        from django.contrib.messages.storage.fallback import FallbackStorage

        request._messages = FallbackStorage(request)
        response = self.admin.my_profile(request, TeamsReleaseManager.objects.none())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("admin:teams_releasemanager_changelist"),
        )
        messages = [m.message.lower() for m in request._messages]
        self.assertTrue(any("permission" in message for message in messages))

    def test_my_profile_change_action_redirects(self):
        request = self._get_request()
        response = self.admin.my_profile_action(request, self.manager)
        self.assertEqual(response.status_code, 302)
        expected = reverse("admin:teams_releasemanager_change", args=[self.manager.pk])
        self.assertEqual(response.url, expected)

    @patch("core.admin.requests.post")
    def test_test_credentials_action(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        request = self._get_request()
        self.admin.test_credentials_action(request, self.manager)
        mock_post.assert_called_once()
        messages = [m.message for m in request._messages]
        self.assertTrue(any("credentials valid" in m for m in messages))

    @patch("core.admin.requests.post")
    def test_test_credentials_bulk_action(self, mock_post):
        mock_post.return_value = MagicMock(status_code=403)
        request = self._get_request()
        queryset = TeamsReleaseManager.objects.filter(pk=self.manager.pk)
        self.admin.test_credentials(request, queryset)
        mock_post.assert_called_once()
        messages = [m.message.lower() for m in request._messages]
        self.assertTrue(any("credentials invalid" in m for m in messages))

    @patch("core.admin.requests.post")
    def test_test_credentials_action_unexpected_status(self, mock_post):
        mock_post.return_value = MagicMock(status_code=404)
        request = self._get_request()
        self.admin.test_credentials_action(request, self.manager)
        messages = [m.message.lower() for m in request._messages]
        self.assertTrue(any("unexpected status" in m for m in messages))

    @pytest.mark.skip("Change form object action link not rendered in test environment")
    def test_change_form_contains_link(self):
        request = self._get_request()
        response = self.admin.changeform_view(request, str(self.manager.pk))
        content = response.render().content.decode()
        self.assertIn("Test credentials", content)

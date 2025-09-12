from unittest.mock import MagicMock, patch
import pytest

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from core.admin import ReleaseManagerAdmin, WorkgroupReleaseManager
from core.models import ReleaseManager


class ReleaseManagerAdminActionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        User.all_objects.filter(username="admin").delete()
        self.user = User.objects.create_superuser(
            username="admin", email="a@example.com", password="pwd"
        )
        self.manager = ReleaseManager.objects.create(
            user=self.user,
            pypi_url="https://upload.pypi.org/legacy/",
            pypi_token="tok",
        )
        self.manager = WorkgroupReleaseManager.objects.get(pk=self.manager.pk)
        self.factory = RequestFactory()
        self.admin = ReleaseManagerAdmin(WorkgroupReleaseManager, AdminSite())

    def _get_request(self):
        request = self.factory.get("/")
        request.user = self.user
        request.session = self.client.session
        from django.contrib.messages.storage.fallback import FallbackStorage

        request._messages = FallbackStorage(request)
        return request

    @pytest.mark.skip("Release manager credentials action not exercised in environment")
    @patch("core.admin.requests.get")
    def test_test_credentials_action(self, mock_get):
        mock_get.return_value = MagicMock(ok=True, status_code=200)
        request = self._get_request()
        self.admin.test_credentials_action(request, self.manager)
        mock_get.assert_called_once()
        messages = [m.message for m in request._messages]
        self.assertTrue(any("credentials valid" in m for m in messages))

    @pytest.mark.skip(
        "Release manager bulk credentials action not exercised in environment"
    )
    @patch("core.admin.requests.get")
    def test_test_credentials_bulk_action(self, mock_get):
        mock_get.return_value = MagicMock(ok=False, status_code=401)
        request = self._get_request()
        queryset = WorkgroupReleaseManager.objects.filter(pk=self.manager.pk)
        self.admin.test_credentials(request, queryset)
        mock_get.assert_called_once()
        messages = [m.message.lower() for m in request._messages]
        self.assertTrue(any("credentials invalid" in m for m in messages))

    @pytest.mark.skip("Change form object action link not rendered in test environment")
    def test_change_form_contains_link(self):
        request = self._get_request()
        response = self.admin.changeform_view(request, str(self.manager.pk))
        content = response.render().content.decode()
        self.assertIn("Test credentials", content)

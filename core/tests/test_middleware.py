from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from core import middleware
from core.middleware import AdminHistoryMiddleware
from core.models import AdminHistory


class AdminHistoryMiddlewareTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.user = self.user_model.objects.create_user(
            username="staff", email="staff@example.com", password="pass", is_staff=True
        )
        self.factory = RequestFactory()

    def _get_request(self, path="/admin/core/adminhistory/"):
        request = self.factory.get(path)
        request.user = self.user
        request.resolver_match = SimpleNamespace(url_name="core_adminhistory_changelist")
        return request

    def test_content_type_uses_model_cache_when_available(self):
        request = self._get_request()
        middleware_instance = AdminHistoryMiddleware(lambda req: HttpResponse(status=200))

        original_get_model = middleware.apps.get_model
        with mock.patch(
            "core.middleware.apps.get_model", side_effect=original_get_model
        ) as get_model, mock.patch(
            "core.middleware.ContentType.objects.get_by_natural_key", side_effect=AssertionError
        ) as get_by_natural_key:
            middleware_instance(request)

        get_model.assert_called_once_with("core", "adminhistory")
        self.assertFalse(get_by_natural_key.called)
        self.assertEqual(AdminHistory.objects.filter(user=self.user).count(), 1)


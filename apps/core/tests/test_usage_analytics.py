from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings

from config.middleware import UsageAnalyticsMiddleware

from apps.core.models import UsageEvent


def _dummy_view(request):
    return HttpResponse("ok")


@override_settings(ENABLE_USAGE_ANALYTICS=True, STATIC_URL="/static/")
class UsageAnalyticsMiddlewareTests(TestCase):
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="middleware-user", password="pass"
        )

    def test_records_usage_event(self):
        request = self.factory.get("/analytics/demo/?foo=bar")
        request.user = self.user
        request.resolver_match = SimpleNamespace(
            view_name=f"{__name__}._dummy_view", func=_dummy_view
        )

        middleware = UsageAnalyticsMiddleware(lambda req: HttpResponse("ok"))
        response = middleware(request)
        self.assertEqual(response.status_code, 200)

        event = UsageEvent.objects.latest("timestamp")
        self.assertEqual(event.view_name, f"{__name__}._dummy_view")
        self.assertEqual(event.app_label, "core")
        self.assertEqual(event.action, UsageEvent.Action.READ)
        self.assertEqual(event.user, self.user)

    def test_skips_static_requests(self):
        request = self.factory.get("/static/app.js")
        request.user = self.user
        middleware = UsageAnalyticsMiddleware(lambda req: HttpResponse("ok"))
        middleware(request)
        self.assertFalse(UsageEvent.objects.exists())


@override_settings(ENABLE_USAGE_ANALYTICS=True)
class UsageAnalyticsSignalTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.user_model = get_user_model()

    def test_model_signals_record_events(self):
        user = self.user_model.objects.create_user(username="signal-user")
        user.first_name = "Updated"
        user.save()
        user.delete()

        actions = list(
            UsageEvent.objects.filter(model_label="users.user")
            .values_list("action", flat=True)
        )
        self.assertIn(UsageEvent.Action.CREATE, actions)
        self.assertIn(UsageEvent.Action.UPDATE, actions)
        self.assertIn(UsageEvent.Action.DELETE, actions)

import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.utils import OperationalError
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from config.middleware import UsageAnalyticsMiddleware

from apps.core.analytics import (
    USAGE_ANALYTICS_FEATURE_SLUG,
    flush_usage_event_buffer,
    record_model_event,
    usage_analytics_enabled,
)
from apps.core.models import UsageEvent
from apps.features.models import Feature


def _dummy_view(request):
    return HttpResponse("ok")


class UsageAnalyticsFeatureToggleMixin:
    @staticmethod
    def _set_usage_analytics_feature(*, enabled: bool) -> None:
        Feature.objects.update_or_create(
            slug=USAGE_ANALYTICS_FEATURE_SLUG,
            defaults={
                "display": "Usage Analytics",
                "is_enabled": enabled,
                "source": Feature.Source.CUSTOM,
            },
        )


@override_settings(ENABLE_USAGE_ANALYTICS=True, STATIC_URL="/static/")
class UsageAnalyticsMiddlewareTests(UsageAnalyticsFeatureToggleMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="middleware-user", password="pass"
        )

    def _build_request(self, path: str = "/analytics/demo/?foo=bar"):
        request = self.factory.get(path)
        request.user = self.user
        request.resolver_match = SimpleNamespace(
            view_name=f"{__name__}._dummy_view", func=_dummy_view
        )
        return request

    def test_records_usage_event_when_suite_feature_enabled(self):
        self._set_usage_analytics_feature(enabled=True)
        request = self._build_request()

        middleware = UsageAnalyticsMiddleware(lambda req: HttpResponse("ok"))
        response = middleware(request)
        self.assertEqual(response.status_code, 200)

        event = UsageEvent.objects.latest("timestamp")
        self.assertEqual(event.view_name, f"{__name__}._dummy_view")
        self.assertEqual(event.app_label, "core")
        self.assertEqual(event.action, UsageEvent.Action.READ)
        self.assertEqual(event.user, self.user)

    def test_skips_request_events_when_suite_feature_disabled(self):
        self._set_usage_analytics_feature(enabled=False)
        request = self._build_request()

        middleware = UsageAnalyticsMiddleware(lambda req: HttpResponse("ok"))
        middleware(request)

        self.assertFalse(
            UsageEvent.objects.filter(view_name=f"{__name__}._dummy_view").exists()
        )

    def test_skips_static_requests(self):
        self._set_usage_analytics_feature(enabled=True)
        request = self.factory.get("/static/app.js")
        request.user = self.user
        middleware = UsageAnalyticsMiddleware(lambda req: HttpResponse("ok"))
        middleware(request)
        self.assertFalse(UsageEvent.objects.exists())


@override_settings(ENABLE_USAGE_ANALYTICS=True)
class UsageAnalyticsSignalTests(UsageAnalyticsFeatureToggleMixin, TestCase):
    def test_model_events_do_not_record_when_disabled(self):
        self._set_usage_analytics_feature(enabled=False)
        before_count = UsageEvent.objects.filter(model_label="users.user").count()

        record_model_event(
            model_label="users.user",
            action=UsageEvent.Action.CREATE,
        )
        flush_usage_event_buffer()

        self.assertEqual(
            UsageEvent.objects.filter(model_label="users.user").count(),
            before_count,
        )

    def test_model_events_resume_when_reenabled(self):
        self._set_usage_analytics_feature(enabled=False)
        self.assertFalse(usage_analytics_enabled())

        before_count = UsageEvent.objects.filter(model_label="users.user").count()

        record_model_event(
            model_label="users.user",
            action=UsageEvent.Action.CREATE,
        )
        flush_usage_event_buffer()
        self.assertEqual(
            UsageEvent.objects.filter(model_label="users.user").count(),
            before_count,
        )

        self._set_usage_analytics_feature(enabled=True)
        self.assertTrue(usage_analytics_enabled())

        record_model_event(model_label="users.user", action=UsageEvent.Action.CREATE)
        record_model_event(model_label="users.user", action=UsageEvent.Action.UPDATE)
        record_model_event(model_label="users.user", action=UsageEvent.Action.DELETE)
        flush_usage_event_buffer()

        actions = list(
            UsageEvent.objects.filter(model_label="users.user").values_list(
                "action", flat=True
            )
        )
        self.assertIn(UsageEvent.Action.CREATE, actions)
        self.assertIn(UsageEvent.Action.UPDATE, actions)
        self.assertIn(UsageEvent.Action.DELETE, actions)


@override_settings(ENABLE_USAGE_ANALYTICS=True)
class UsageAnalyticsReadSurfaceTests(UsageAnalyticsFeatureToggleMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.staff_user = get_user_model().objects.create_user(
            username="analytics-staff",
            password="pass",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.staff_user)
        self.summary_url = reverse("usage-analytics-summary")

    @staticmethod
    def _create_usage_event() -> UsageEvent:
        return UsageEvent.objects.create(
            app_label="core",
            view_name="apps.core.views.usage_analytics.usage_analytics_summary",
            path="/core/usage-analytics/summary/?days=7",
            method="GET",
            status_code=200,
            action=UsageEvent.Action.READ,
            metadata={"status_text": "OK"},
        )

    def test_summary_view_stays_available_when_collection_disabled(self):
        self._create_usage_event()
        self._set_usage_analytics_feature(enabled=False)

        response = self.client.get(self.summary_url, {"days": 7})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["analytics_enabled"])
        self.assertTrue(payload["collection_paused"])
        self.assertEqual(payload["top_apps"][0]["app_label"], "core")
        self.assertEqual(payload["top_apps"][0]["count"], 1)

    def test_management_command_exports_existing_history_when_collection_disabled(self):
        self._create_usage_event()
        self._set_usage_analytics_feature(enabled=False)

        stdout = StringIO()
        call_command("analytics", "--days", "7", stdout=stdout)
        payload = json.loads(stdout.getvalue())

        self.assertFalse(payload["analytics_enabled"])
        self.assertTrue(payload["collection_paused"])
        self.assertEqual(
            payload["top_views"][0]["view_name"],
            "apps.core.views.usage_analytics.usage_analytics_summary",
        )


@override_settings(ENABLE_USAGE_ANALYTICS=False)
class UsageAnalyticsBootstrapFallbackTests(TestCase):
    def test_usage_analytics_helper_falls_back_to_settings_when_feature_table_unavailable(
        self,
    ):
        with patch(
            "apps.features.utils.Feature.objects.filter",
            side_effect=OperationalError("no such table: features_feature"),
        ):
            self.assertFalse(usage_analytics_enabled())


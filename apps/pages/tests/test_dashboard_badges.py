from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from apps.counters.badge_utils import BadgeCounterResult
from apps.counters.models import BadgeCounter


def primary_value(_counter):
    return BadgeCounterResult(primary=7, secondary=3, label="Accounts")


def secondary_value(_counter):
    return 3


class DashboardBadgeTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.superuser = self.user_model.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(self.superuser)
        self.content_type = ContentType.objects.get_for_model(self.user_model)

    def test_dashboard_rows_include_htmx_loader(self):
        response = self.client.get(reverse("admin:index"))

        self.assertContains(response, "data-model-status-loader")
        self.assertContains(response, 'hx-trigger="revealed"')
        self.assertContains(
            response,
            reverse("admin:dashboard_model_status"),
        )

    def test_dashboard_status_view_renders_badge_counters(self):
        BadgeCounter.objects.create(
            content_type=self.content_type,
            name="users",
            primary_source_type=BadgeCounter.ValueSource.CALLABLE,
            primary_source="apps.pages.tests.test_dashboard_badges.primary_value",
            secondary_source_type=BadgeCounter.ValueSource.CALLABLE,
            secondary_source="apps.pages.tests.test_dashboard_badges.secondary_value",
            css_class="text-bg-info",
        )
        BadgeCounter.invalidate_model_cache(self.content_type)

        response = self.client.get(
            reverse("admin:dashboard_model_status"),
            {"app": self.content_type.app_label, "model": self.user_model.__name__},
        )

        self.assertContains(response, "badge-counter")
        self.assertContains(response, "Accounts")
        self.assertContains(response, "7 / 3")

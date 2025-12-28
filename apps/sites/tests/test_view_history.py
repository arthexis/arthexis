from django.contrib.sites.models import Site
from io import StringIO
from datetime import timedelta

from django.core.management import call_command
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.sites.middleware import ViewHistoryMiddleware
from apps.sites.models import ViewHistory


class ViewHistoryModelTests(TestCase):
    def test_allows_long_paths(self):
        """Long request paths should be stored instead of triggering DB errors."""

        long_path = "/" + ("a" * 1500)

        entry = ViewHistory.objects.create(
            path=long_path,
            method="GET",
            status_code=200,
            status_text="OK",
        )

        self.assertEqual(ViewHistory.objects.count(), 1)
        self.assertEqual(entry.path, long_path)


class ViewHistoryMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = Site.objects.update_or_create(
            pk=1, defaults={"domain": "testserver", "name": "Test Server"}
        )[0]

    def _middleware(self):
        return ViewHistoryMiddleware(lambda request: HttpResponse("ok"))

    def test_tracks_admin_requests(self):
        request = self.factory.get("/admin/", HTTP_HOST=self.site.domain)
        request.site = self.site

        response = self._middleware()(request)

        self.assertEqual(response.status_code, 200)
        entry = ViewHistory.objects.latest("visited_at")
        self.assertEqual(entry.kind, ViewHistory.Kind.ADMIN)
        self.assertEqual(entry.site, self.site)

    def test_tracks_site_requests_with_site_association(self):
        request = self.factory.get("/welcome/", HTTP_HOST=self.site.domain)
        request.site = self.site

        response = self._middleware()(request)

        self.assertEqual(response.status_code, 200)
        entry = ViewHistory.objects.latest("visited_at")
        self.assertEqual(entry.kind, ViewHistory.Kind.SITE)
        self.assertEqual(entry.site, self.site)

    def test_records_exception_name_on_error(self):
        def failing_view(request):
            raise ValueError("boom")

        middleware = ViewHistoryMiddleware(failing_view)
        request = self.factory.get("/crash/")

        with self.assertRaises(ValueError):
            middleware(request)

        entry = ViewHistory.objects.latest("visited_at")
        self.assertEqual(entry.status_code, 500)
        self.assertEqual(entry.exception_name, "ValueError")


class ViewErrorsCommandTests(TestCase):
    def setUp(self):
        now = timezone.now()
        for index in range(6):
            ViewHistory.objects.create(
                path=f"/error/{index}/",
                method="GET",
                status_code=500,
                status_text="Server Error",
                error_message="boom",
                exception_name="ValueError",
                visited_at=now + timedelta(minutes=index),
            )

    def test_displays_last_five_errors_by_default(self):
        output = StringIO()
        call_command("view_errors", stdout=output)

        lines = [line for line in output.getvalue().strip().splitlines() if line]
        # Header + separator + 5 rows
        self.assertGreaterEqual(len(lines), 7)
        self.assertIn("/error/5/", lines[2])
        self.assertIn("/error/1/", lines[-1])

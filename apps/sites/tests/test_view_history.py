from django.contrib.sites.models import Site
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

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

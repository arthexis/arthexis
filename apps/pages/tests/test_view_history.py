from django.test import TestCase

from apps.pages.models import ViewHistory


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

from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

import requests

from core.models import Reference
from core.tasks import validate_reference_links


class ReferenceValidationTaskTests(TestCase):
    @mock.patch("core.tasks.requests.get")
    def test_validates_stale_and_unvalidated_links(self, mock_get):
        now = timezone.now()
        fresh = Reference.objects.create(
            alt_text="Fresh",
            value="https://example.com/fresh",
            validated_url_at=now,
        )
        stale = Reference.objects.create(
            alt_text="Stale",
            value="https://example.com/stale",
            validated_url_at=now - timedelta(days=8),
        )
        missing = Reference.objects.create(
            alt_text="Missing",
            value="https://example.com/missing",
        )

        success_response = mock.Mock(status_code=200)
        mock_get.side_effect = [success_response, requests.RequestException("boom")]

        processed = validate_reference_links()

        self.assertEqual(processed, 2)
        self.assertEqual(mock_get.call_count, 2)

        stale.refresh_from_db()
        missing.refresh_from_db()
        fresh.refresh_from_db()

        self.assertEqual(stale.validation_status, 200)
        self.assertGreaterEqual(stale.validated_url_at, now)

        self.assertEqual(missing.validation_status, 0)
        self.assertIsNotNone(missing.validated_url_at)

        self.assertIsNone(fresh.validation_status)
        self.assertEqual(fresh.validated_url_at, now)

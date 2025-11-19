from datetime import timedelta
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from protocols.models import MediaBucket


class MediaBucketTests(TestCase):
    def test_bucket_pattern_and_size_filters(self):
        bucket = MediaBucket.objects.create(
            allowed_patterns="*.log\nerrors-*.txt", max_bytes=10
        )
        self.assertTrue(bucket.allows_filename("report.log"))
        self.assertTrue(bucket.allows_filename("errors-001.txt"))
        self.assertFalse(bucket.allows_filename("image.png"))
        self.assertTrue(bucket.allows_size(10))
        self.assertFalse(bucket.allows_size(11))

    @override_settings(MEDIA_ROOT="/tmp/protocols-media")
    def test_upload_view_rejects_invalid_files_and_saves_valid_upload(self):
        client = Client()
        expired_bucket = MediaBucket.objects.create(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        url = reverse("protocols:media-bucket-upload", args=[expired_bucket.slug])
        response = client.post(url, {"file": SimpleUploadedFile("diag.log", b"data")})
        self.assertEqual(response.status_code, 410)

        bucket = MediaBucket.objects.create(
            allowed_patterns="*.log",
            max_bytes=5,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        url = reverse("protocols:media-bucket-upload", args=[bucket.slug])

        oversized = SimpleUploadedFile("diag.log", b"0123456789")
        response = client.post(url, {"file": oversized})
        self.assertEqual(response.status_code, 400)

        wrong_type = SimpleUploadedFile("diag.txt", b"diag")
        response = client.post(url, {"file": wrong_type})
        self.assertEqual(response.status_code, 400)

        accepted = SimpleUploadedFile("diag.log", b"ok")
        response = client.post(url, {"file": accepted})
        self.assertEqual(response.status_code, 201)
        bucket.refresh_from_db()
        self.assertEqual(bucket.files.count(), 1)
        media_file = bucket.files.first()
        self.assertIsNotNone(media_file)
        saved_path = Path(media_file.file.path)
        self.assertTrue(saved_path.exists())
        self.assertEqual(saved_path.read_bytes(), b"ok")

from io import BytesIO
from tempfile import TemporaryDirectory

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import clear_url_caches
from PIL import Image

from apps.media.utils import create_media_file, ensure_media_bucket
from apps.modules.models import Module, get_module_favicon_bucket
from apps.sites.models import SiteBadge, get_site_badge_favicon_bucket


class ProtectedMediaServingTests(TestCase):
    def setUp(self):
        self._media_dir = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._media_dir.cleanup)
        override = override_settings(
            DEBUG=False,
            MEDIA_ROOT=self._media_dir.name,
            MEDIA_URL="/media/",
        )
        override.enable()
        clear_url_caches()
        self.addCleanup(clear_url_caches)
        self.addCleanup(override.disable)
        self.user = get_user_model().objects.create_user(
            username="media-owner",
            password="pw",
        )
        self.other_user = get_user_model().objects.create_user(
            username="media-other",
            password="pw",
        )

    def _upload(self, name="media.jpg"):
        buffer = BytesIO()
        Image.new("RGB", (10, 10), "blue").save(buffer, format="JPEG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")

    def _save_direct_file(self, path: str):
        default_storage.save(path, ContentFile(b"direct file payload"))
        return f"{settings.MEDIA_URL.rstrip('/')}/{path.lstrip('/')}"

    def test_authenticated_user_cannot_fetch_unrelated_media_file(self):
        bucket = ensure_media_bucket(slug="private-bucket", name="Private Bucket")
        media_file = create_media_file(
            bucket=bucket,
            uploaded_file=self._upload("unrelated.jpg"),
        )
        self.client.force_login(self.user)

        response = self.client.get(media_file.file.url)

        self.assertEqual(response.status_code, 403)

    def test_inactive_staff_user_cannot_fetch_unrelated_media_file(self):
        inactive_staff = get_user_model().objects.create_user(
            username="inactive-media-staff",
            password="pw",
            is_staff=True,
            is_active=False,
        )
        bucket = ensure_media_bucket(
            slug="inactive-staff-private-bucket",
            name="Inactive Staff Private Bucket",
        )
        media_file = create_media_file(
            bucket=bucket,
            uploaded_file=self._upload("inactive-staff.jpg"),
        )
        self.client.force_login(inactive_staff)

        response = self.client.get(media_file.file.url)

        self.assertEqual(response.status_code, 403)

    def test_active_staff_user_can_fetch_direct_filefield_media(self):
        staff_user = get_user_model().objects.create_user(
            username="active-media-staff",
            password="pw",
            is_staff=True,
        )
        url = self._save_direct_file("sites/user_story_screenshots/direct-staff.jpg")
        self.client.force_login(staff_user)

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
    def test_inactive_staff_user_cannot_fetch_direct_filefield_media(self):
        inactive_staff = get_user_model().objects.create_user(
            username="inactive-direct-media-staff",
            password="pw",
            is_staff=True,
            is_active=False,
        )
        url = self._save_direct_file(
            "sites/user_story_screenshots/inactive-direct-staff.jpg"
        )
        self.client.force_login(inactive_staff)

        response = self.client.get(url)

        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_can_fetch_configured_site_favicon(self):
        media_file = create_media_file(
            bucket=get_site_badge_favicon_bucket(),
            uploaded_file=self._upload("site-favicon.png"),
        )
        site, _created = Site.objects.get_or_create(
            domain="media-favicon.example",
            defaults={"name": "Media Favicon"},
        )
        SiteBadge.objects.create(site=site, favicon_media=media_file)

        response = self.client.get(media_file.file.url)

        self.assertEqual(response.status_code, 200)
    def test_anonymous_user_can_fetch_configured_module_favicon(self):
        media_file = create_media_file(
            bucket=get_module_favicon_bucket(),
            uploaded_file=self._upload("module-favicon.png"),
        )
        Module.objects.create(
            menu="Favicon Module",
            path="/favicon-module/",
            favicon_media=media_file,
        )

        response = self.client.get(media_file.file.url)

        self.assertEqual(response.status_code, 200)

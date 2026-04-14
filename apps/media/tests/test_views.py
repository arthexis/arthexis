"""Tests for media upload views."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.media.models import MediaBucket, MediaFile


pytestmark = pytest.mark.django_db


def _upload_url(bucket: MediaBucket) -> str:
    return reverse("ocpp:media-bucket-upload", kwargs={"slug": bucket.slug})


def _build_upload() -> SimpleUploadedFile:
    return SimpleUploadedFile("test.txt", b"hello", content_type="text/plain")


def test_media_bucket_upload_rejects_anonymous_for_non_expiring_bucket(client) -> None:
    bucket = MediaBucket.objects.create(slug="persistent-bucket", name="Persistent")

    response = client.post(_upload_url(bucket), {"file": _build_upload()})

    assert response.status_code == 403
    assert response.json()["detail"] == "authentication is required for this bucket"
    assert MediaFile.objects.count() == 0


def test_media_bucket_upload_allows_anonymous_for_expiring_bucket(client) -> None:
    bucket = MediaBucket.objects.create(
        slug="expiring-bucket",
        name="Expiring",
        expires_at=timezone.now() + timedelta(hours=1),
    )

    response = client.post(_upload_url(bucket), {"file": _build_upload()})

    assert response.status_code == 201
    assert MediaFile.objects.count() == 1


def test_media_bucket_upload_allows_authenticated_for_non_expiring_bucket(client) -> None:
    user = get_user_model().objects.create_user(username="alice", password="test-pass-123")
    bucket = MediaBucket.objects.create(slug="auth-bucket", name="Auth")
    client.force_login(user)

    response = client.post(_upload_url(bucket), {"file": _build_upload()})

    assert response.status_code == 201
    assert MediaFile.objects.count() == 1

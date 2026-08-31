from unittest.mock import patch

import pytest
from django.contrib import admin
from django.urls import NoReverseMatch

from apps.media.admin import MediaBucketAdmin
from apps.media.models import MediaBucket

pytestmark = pytest.mark.django_db


def test_media_bucket_upload_endpoint_is_blank_when_ocpp_route_is_disabled():
    bucket = MediaBucket.objects.create(slug="admin-bucket", name="Admin Bucket")
    bucket_admin = MediaBucketAdmin(MediaBucket, admin.site)

    with patch("apps.media.admin.reverse", side_effect=NoReverseMatch):
        assert bucket_admin.upload_endpoint(bucket) == ""

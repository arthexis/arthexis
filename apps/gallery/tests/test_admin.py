"""Regression tests for gallery admin actions."""

from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.gallery.services import create_gallery_image


def _upload(name: str = "gallery-admin.jpg") -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", (10, 10), "purple").save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


@pytest.mark.django_db
def test_gallery_image_change_form_includes_uploader_object_tool(admin_client):
    """Regression: gallery change form should include a direct uploader shortcut."""

    user = get_user_model().objects.create_user(username="gallery-admin-owner")
    image = create_gallery_image(
        uploaded_file=_upload(),
        title="Admin Gallery Image",
        owner_user=user,
    )

    response = admin_client.get(reverse("admin:gallery_galleryimage_change", args=[image.pk]))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "Uploader" in body
    assert reverse("admin:content_contentsample_changelist") in body

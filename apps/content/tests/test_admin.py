from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.content.models import ContentSample


def test_admin_base_site_exposes_drop_upload_overlay(admin_client):
    response = admin_client.get(reverse("admin:index"))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'data-content-sample-drop-overlay' in content
    assert reverse("admin:content_contentsample_drop_upload") in content


def test_drop_upload_creates_content_sample_and_returns_change_url(
    admin_client,
    admin_user,
    tmp_path,
):
    original_log_dir = settings.LOG_DIR
    original_count = ContentSample.objects.count()
    settings.LOG_DIR = tmp_path
    try:
        response = admin_client.post(
            reverse("admin:content_contentsample_drop_upload"),
            {
                "file": SimpleUploadedFile(
                    "notes.txt",
                    b"hello drag and drop",
                    content_type="text/plain",
                )
            },
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
    finally:
        settings.LOG_DIR = original_log_dir

    assert response.status_code == 201
    payload = response.json()

    assert ContentSample.objects.count() == original_count + 1

    sample = ContentSample.objects.get(pk=payload["sample_id"])
    assert sample.user == admin_user
    assert sample.kind == ContentSample.TEXT
    assert sample.method == "ADMIN_DROP"
    assert sample.content == "hello drag and drop"
    assert payload["change_url"] == reverse(
        "admin:content_contentsample_change",
        args=[sample.pk],
    )

    sample_path = Path(sample.path)
    assert sample_path.is_absolute()
    assert sample_path.exists()
    assert sample_path.read_text() == "hello drag and drop"


def test_drop_upload_rejects_files_that_exceed_size_limit(
    admin_client,
    settings,
    tmp_path,
):
    settings.LOG_DIR = tmp_path
    settings.CONTENT_DROP_MAX_UPLOAD_SIZE = 8
    original_count = ContentSample.objects.count()

    response = admin_client.post(
        reverse("admin:content_contentsample_drop_upload"),
        {
            "file": SimpleUploadedFile(
                "notes.txt",
                b"0123456789",
                content_type="text/plain",
            )
        },
        HTTP_ACCEPT="application/json",
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 400
    assert response.json() == {"error": "File exceeds the allowed size."}
    assert ContentSample.objects.count() == original_count
    assert not list(tmp_path.glob("content-drops/*"))

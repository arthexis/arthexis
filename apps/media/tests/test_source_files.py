"""Tests for managed media source files."""

from __future__ import annotations

import hashlib
from io import StringIO

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import RequestFactory

from apps.media.admin import MediaFileAdmin, MediaSourceFileAdmin
from apps.media.models import MediaBucket, MediaFile, MediaSourceFile
from apps.media.utils import copy_media_source_file_from_path, create_media_source_file

pytestmark = pytest.mark.django_db


def test_create_media_source_file_from_upload(settings, tmp_path) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    upload = SimpleUploadedFile(
        "Reality Fracture Six.mse-set",
        b"mse payload",
        content_type="application/zip",
    )

    source_file = create_media_source_file(uploaded_file=upload)

    assert source_file.name == "Reality Fracture Six.mse-set".removesuffix(".mse-set")
    assert source_file.original_name == "Reality Fracture Six.mse-set"
    assert source_file.content_type == "application/zip"
    assert source_file.size == len(b"mse payload")
    assert source_file.checksum_sha256
    assert source_file.file.name.startswith("protocols/source-files/")
    assert source_file.file.read() == b"mse payload"


def test_media_source_file_save_computes_checksum_for_direct_upload(
    settings, tmp_path
) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"

    source_file = MediaSourceFile.objects.create(
        file=SimpleUploadedFile("direct.mse-set", b"direct archive"),
    )

    assert source_file.checksum_sha256
    assert source_file.original_name == "direct.mse-set"


def test_media_source_file_save_refreshes_metadata_when_file_changes(
    settings, tmp_path
) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    source_file = MediaSourceFile.objects.create(
        file=SimpleUploadedFile(
            "first.mse-set", b"first", content_type="application/zip"
        ),
    )

    source_file.file = SimpleUploadedFile(
        "second.mse-set", b"second payload", content_type="application/x-mse"
    )
    source_file.save()

    assert source_file.original_name == "second.mse-set"
    assert source_file.name == "second"
    assert source_file.content_type == "application/x-mse"
    assert source_file.size == len(b"second payload")
    assert source_file.checksum_sha256 == hashlib.sha256(b"second payload").hexdigest()


def test_media_source_file_save_guesses_content_type_for_non_uploaded_files(
    settings, tmp_path
) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"

    source_file = MediaSourceFile.objects.create(
        file=ContentFile(b"<svg></svg>", name="diagram.svg"),
    )

    assert source_file.content_type == "image/svg+xml"


def test_copy_media_source_file_from_path(settings, tmp_path) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    local_file = tmp_path / "Reality Fracture Six.mse-set"
    local_file.write_bytes(b"mse archive bytes")

    source_file = copy_media_source_file_from_path(local_file)

    assert source_file.source_type == MediaSourceFile.SourceType.MSE_SET
    assert source_file.original_name == local_file.name
    assert source_file.source_uri == local_file.as_posix()
    assert source_file.file.path != local_file.as_posix()
    assert source_file.file.read() == b"mse archive bytes"


def test_media_file_can_reference_shared_source_file(settings, tmp_path) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    source_file = create_media_source_file(
        uploaded_file=SimpleUploadedFile("cards.mse-set", b"archive"),
    )
    bucket = MediaBucket.objects.create(slug="mse-images", name="MSE Images")

    first = MediaFile.objects.create(
        bucket=bucket,
        file=SimpleUploadedFile("image1.png", b"first", content_type="image/png"),
        source_file=source_file,
        source_member="image1.png",
    )
    second = MediaFile.objects.create(
        bucket=bucket,
        file=SimpleUploadedFile("image2.png", b"second", content_type="image/png"),
        source_file=source_file,
        source_member="image2.png",
    )

    assert first.source_file == source_file
    assert second.source_file == source_file
    assert list(source_file.derived_files.order_by("source_member")) == [first, second]


def test_media_source_file_admin_annotates_derived_file_count(
    settings, tmp_path
) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    source_file = create_media_source_file(
        uploaded_file=SimpleUploadedFile("cards.mse-set", b"archive"),
    )
    bucket = MediaBucket.objects.create(slug="mse-images", name="MSE Images")
    MediaFile.objects.create(
        bucket=bucket,
        file=SimpleUploadedFile("image1.png", b"first", content_type="image/png"),
        source_file=source_file,
    )
    MediaFile.objects.create(
        bucket=bucket,
        file=SimpleUploadedFile("image2.png", b"second", content_type="image/png"),
        source_file=source_file,
    )
    model_admin = MediaSourceFileAdmin(MediaSourceFile, AdminSite())
    request = RequestFactory().get("/admin/media/mediasourcefile/")

    annotated_source = model_admin.get_queryset(request).get(pk=source_file.pk)

    assert annotated_source.derived_file_total == 2
    assert model_admin.derived_file_count(annotated_source) == 2


def test_media_file_admin_selects_related_source_file_and_bucket() -> None:
    model_admin = MediaFileAdmin(MediaFile, AdminSite())

    assert model_admin.list_select_related == ("bucket", "source_file")


def test_copy_media_source_file_command(settings, tmp_path) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    local_file = tmp_path / "cards.mse-set"
    local_file.write_bytes(b"archive")

    stdout = StringIO()
    call_command("copy_media_source_file", str(local_file), stdout=stdout)

    source_file = MediaSourceFile.objects.get()
    assert source_file.original_name == "cards.mse-set"
    assert "Copied source file" in stdout.getvalue()

from __future__ import annotations

from io import BytesIO, StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.validators import validate_slug
from PIL import Image

from apps.classification.ingest import (
    SUPPORTED_IMAGE_EXTENSIONS,
    SUPPORTED_IMAGE_PATTERNS,
)
from apps.classification.management.commands import import_training_images
from apps.classification.management.commands.import_training_images import (
    IMAGE_EXTENSIONS,
    Command,
)
from apps.classification.models import ClassificationTag, TrainingSample
from apps.media.models import MediaFile


def _write_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (16, 16), color=color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    path.write_bytes(buffer.getvalue())


def test_supported_image_patterns_are_derived_from_extensions():
    assert IMAGE_EXTENSIONS == set(SUPPORTED_IMAGE_EXTENSIONS)
    assert SUPPORTED_IMAGE_PATTERNS.splitlines() == [
        f"*{extension}" for extension in SUPPORTED_IMAGE_EXTENSIONS
    ]


@pytest.mark.django_db
def test_import_training_images_creates_unverified_samples_from_parent_folders(tmp_path):
    _write_image(tmp_path / "cats" / "cat-a.png", (200, 20, 20))
    _write_image(tmp_path / "dogs" / "dog-a.png", (20, 20, 200))
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")

    stdout = StringIO()
    call_command("import_training_images", str(tmp_path), stdout=stdout)

    assert ClassificationTag.objects.filter(slug__in=["cats", "dogs"]).count() == 2
    assert MediaFile.objects.count() == 2
    assert TrainingSample.objects.count() == 2
    assert TrainingSample.objects.filter(is_verified=False).count() == 2
    assert "samples_created=2" in stdout.getvalue()
    assert "skipped_extension=1" in stdout.getvalue()


@pytest.mark.django_db
def test_import_training_images_compacts_long_folder_labels(tmp_path):
    prefix_a = "alpha-" * 18
    prefix_b = "bravo-" * 18
    shared_suffix = "shared-long-training-label"
    _write_image(tmp_path / f"{prefix_a}{shared_suffix}" / "first.png", (200, 20, 20))
    _write_image(tmp_path / f"{prefix_b}{shared_suffix}" / "second.png", (20, 20, 200))

    call_command("import_training_images", str(tmp_path))

    tags = list(ClassificationTag.objects.order_by("slug"))
    assert len(tags) == 2
    assert {len(tag.slug) for tag in tags} == {ClassificationTag._meta.get_field("slug").max_length}
    assert all(len(tag.name) <= ClassificationTag._meta.get_field("name").max_length for tag in tags)
    assert all(":" not in tag.slug for tag in tags)
    for tag in tags:
        validate_slug(tag.slug)
    assert tags[0].slug != tags[1].slug
    assert TrainingSample.objects.count() == 2


@pytest.mark.django_db
def test_import_training_images_compacts_long_explicit_tag(tmp_path):
    _write_image(tmp_path / "seed" / "first.png", (20, 200, 20))
    explicit_tag = f"{'explicit-' * 20}training-tag"

    call_command("import_training_images", str(tmp_path), "--tag", explicit_tag)

    tag = ClassificationTag.objects.get()
    assert len(tag.slug) == ClassificationTag._meta.get_field("slug").max_length
    assert len(tag.name) <= ClassificationTag._meta.get_field("name").max_length
    assert ":" not in tag.slug
    validate_slug(tag.slug)
    assert TrainingSample.objects.count() == 1


@pytest.mark.django_db
def test_import_training_images_can_verify_and_reuse_existing_samples(tmp_path):
    _write_image(tmp_path / "seed" / "first.png", (20, 200, 20))

    call_command("import_training_images", str(tmp_path), "--tag", "seed-image")
    call_command("import_training_images", str(tmp_path), "--tag", "seed-image", "--verified")

    assert ClassificationTag.objects.get().slug == "seed-image"
    assert MediaFile.objects.count() == 1
    sample = TrainingSample.objects.get()
    assert sample.is_verified is True


@pytest.mark.django_db
def test_import_training_images_imports_changed_file_with_same_name_and_size(tmp_path, monkeypatch):
    image_path = tmp_path / "seed" / "first.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"first-payload")

    monkeypatch.setattr(Command, "_is_readable_image", lambda self, path: True)

    call_command("import_training_images", str(tmp_path), "--tag", "seed-image")
    image_path.write_bytes(b"secondpayload")
    call_command("import_training_images", str(tmp_path), "--tag", "seed-image")

    assert MediaFile.objects.count() == 2
    assert TrainingSample.objects.count() == 2


@pytest.mark.django_db
def test_import_training_images_reuses_same_file_bytes(tmp_path, monkeypatch):
    image_path = tmp_path / "seed" / "first.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"same-payload")

    monkeypatch.setattr(Command, "_is_readable_image", lambda self, path: True)

    call_command("import_training_images", str(tmp_path), "--tag", "seed-image")
    call_command("import_training_images", str(tmp_path), "--tag", "seed-image")

    assert MediaFile.objects.count() == 1
    assert TrainingSample.objects.count() == 1


@pytest.mark.django_db
def test_import_training_images_counts_decompression_bombs_as_unreadable(tmp_path, monkeypatch):
    image_path = tmp_path / "oversized.png"
    image_path.write_bytes(b"oversized")

    def raise_decompression_bomb(path):
        raise Image.DecompressionBombError("too large")

    monkeypatch.setattr(import_training_images.Image, "open", raise_decompression_bomb)

    stdout = StringIO()
    call_command("import_training_images", str(tmp_path), stdout=stdout)

    assert "skipped_unreadable=1" in stdout.getvalue()
    assert MediaFile.objects.count() == 0


@pytest.mark.django_db
def test_import_training_images_dry_run_does_not_create_records(tmp_path):
    _write_image(tmp_path / "root-a.png", (200, 200, 20))

    stdout = StringIO()
    call_command("import_training_images", str(tmp_path), "--dry-run", stdout=stdout)

    assert "dry-run complete" in stdout.getvalue()
    assert MediaFile.objects.count() == 0
    assert TrainingSample.objects.count() == 0


@pytest.mark.django_db
def test_import_training_images_caches_repeated_tag_lookups(tmp_path, monkeypatch):
    _write_image(tmp_path / "same-label" / "first.png", (20, 200, 20))
    _write_image(tmp_path / "same-label" / "second.png", (20, 120, 20))

    original_get_or_create = ClassificationTag.objects.get_or_create
    calls = []

    def counting_get_or_create(*args, **kwargs):
        calls.append((args, kwargs))
        return original_get_or_create(*args, **kwargs)

    monkeypatch.setattr(ClassificationTag.objects, "get_or_create", counting_get_or_create)

    call_command("import_training_images", str(tmp_path))

    assert len(calls) == 1
    assert ClassificationTag.objects.get().slug == "same-label"
    assert TrainingSample.objects.count() == 2

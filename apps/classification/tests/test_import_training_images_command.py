from __future__ import annotations

from io import BytesIO, StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from PIL import Image

from apps.classification.models import ClassificationTag, TrainingSample
from apps.media.models import MediaFile


def _write_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (16, 16), color=color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    path.write_bytes(buffer.getvalue())


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
def test_import_training_images_can_verify_and_reuse_existing_samples(tmp_path):
    _write_image(tmp_path / "seed" / "first.png", (20, 200, 20))

    call_command("import_training_images", str(tmp_path), "--tag", "seed-image")
    call_command("import_training_images", str(tmp_path), "--tag", "seed-image", "--verified")

    assert ClassificationTag.objects.get().slug == "seed-image"
    assert MediaFile.objects.count() == 1
    sample = TrainingSample.objects.get()
    assert sample.is_verified is True


@pytest.mark.django_db
def test_import_training_images_dry_run_does_not_create_records(tmp_path):
    _write_image(tmp_path / "root-a.png", (200, 200, 20))

    stdout = StringIO()
    call_command("import_training_images", str(tmp_path), "--dry-run", stdout=stdout)

    assert "dry-run complete" in stdout.getvalue()
    assert MediaFile.objects.count() == 0
    assert TrainingSample.objects.count() == 0

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.cards.forms import CardFaceAdminForm
from apps.cards.models import CardFace, get_cardface_bucket
from apps.media.models import MediaFile


def _cardface_data(**overrides):
    payload = {
        "name": "Card face",
        "overlay_one_font_size": 28,
        "overlay_one_x": 0,
        "overlay_one_y": 0,
        "overlay_two_font_size": 24,
        "overlay_two_x": 0,
        "overlay_two_y": 0,
    }
    payload.update(overrides)
    return payload


def _image_file(mode="1", size=(64, 64), name="bg.png"):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new(mode, size=size, color=1).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


@pytest.mark.django_db
def test_cardface_form_accepts_existing_background_media_without_upload():
    bucket = get_cardface_bucket()
    media = MediaFile.objects.create(
        bucket=bucket,
        file=_image_file(name="existing.png"),
        original_name="existing.png",
        content_type="image/png",
        size=32,
    )
    form = CardFaceAdminForm(data=_cardface_data(name="Existing media", background_media=media.pk))

    assert form.is_valid()
    instance = form.save(commit=False)
    assert instance.background_media_id == media.pk


@pytest.mark.django_db
def test_cardface_form_upload_creates_media_file():
    form = CardFaceAdminForm(
        data=_cardface_data(name="Uploaded media"),
        files={"background_upload": _image_file(name="fresh.png")},
    )

    assert form.is_valid()
    instance = form.save(commit=False)
    assert instance.background_media is not None
    assert instance.background_media.original_name == "fresh.png"

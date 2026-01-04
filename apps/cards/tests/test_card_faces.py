import io

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from apps.cards.models import CardFace

pytestmark = pytest.mark.django_db


def _image_file(mode="1", size=(64, 64), name="bg.png"):
    buffer = io.BytesIO()
    Image.new(mode, size=size, color=1).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def _oversized_image_file():
    buffer = io.BytesIO()
    Image.new("CMYK", size=(1400, 1400), color=1).save(buffer, format="TIFF")
    return SimpleUploadedFile("huge.tiff", buffer.getvalue(), content_type="image/tiff")


def test_card_face_rejects_invalid_modes_and_size():
    rgb_file = _image_file(mode="RGB")
    face = CardFace(name="Bad mode", background=rgb_file)
    with pytest.raises(ValidationError):
        face.full_clean()

    oversize = CardFace(name="Too big", background=_oversized_image_file())
    with pytest.raises(ValidationError):
        oversize.full_clean()


def test_fixed_back_relations_are_mutual():
    face_a = CardFace.objects.create(name="Front", background=_image_file(name="front.png"))
    face_b = CardFace.objects.create(name="Back", background=_image_file(name="back.png"))

    face_a.fixed_back = face_b
    face_a.save()

    face_b.refresh_from_db()
    assert face_b.fixed_back_id == face_a.pk


def test_resolve_text_overrides_and_fallback():
    text = "Hello [UNKNOWN.VALUE] and [KNOWN.VALUE]"
    resolved = CardFace.resolve_text(text, overrides={"known.value": "ok"})

    assert "[unknown.value]" in resolved
    assert "ok" in resolved


def test_admin_preview_renders_image_and_sigils(client: Client):
    admin_user = get_user_model().objects.create_superuser(
        username="admin", email="admin@example.com", password="pass"
    )
    client.force_login(admin_user)

    face = CardFace.objects.create(
        name="Previewed",
        background=_image_file(name="preview.png"),
        overlay_one_text="ID [CARD.VALUE]",
    )

    url = reverse("admin:cards_cardface_preview", args=[face.pk])
    response = client.get(url)

    assert response.status_code == 200
    assert b"data:image/png;base64" in response.content
    assert b"sigil_card_value" in response.content

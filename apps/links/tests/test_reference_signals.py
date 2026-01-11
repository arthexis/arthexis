import uuid

import pytest
from django.test.utils import override_settings
from django.utils import timezone

from apps.links.models import Reference


@pytest.mark.django_db
def test_reference_qr_image_generated(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        reference = Reference.objects.create(
            alt_text="QR",
            value="https://example.com",
        )

        assert reference.image.name
        assert (tmp_path / reference.image.name).exists()


@pytest.mark.django_db
def test_reference_qr_image_skipped_for_raw_save(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        reference = Reference(
            alt_text="Raw",
            value="https://example.com",
            content_type=Reference.TEXT,
            created=timezone.now(),
            transaction_uuid=uuid.uuid4(),
        )
        reference.save_base(raw=True, force_insert=True)

        assert reference.image.name == ""

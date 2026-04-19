"""Tests for the generic ReferenceAttachment model."""

from __future__ import annotations

from uuid import uuid4

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError

from apps.links.models import Reference, ReferenceAttachment


pytestmark = pytest.mark.django_db


def test_reference_attachment_accepts_uuid_object_ids() -> None:
    reference = Reference.objects.create(
        alt_text="UUID target",
        value="https://example.com/uuid-target",
        method="link",
    )
    object_id = str(uuid4())

    attachment = ReferenceAttachment.objects.create(
        content_type=ContentType.objects.get_for_model(Reference),
        object_id=object_id,
        reference=reference,
    )

    attachment.refresh_from_db()
    assert attachment.object_id == object_id


def test_reference_attachment_prevents_duplicate_reference_links() -> None:
    reference = Reference.objects.create(
        alt_text="Duplicate target",
        value="https://example.com/duplicate-target",
        method="link",
    )
    object_id = str(uuid4())
    content_type = ContentType.objects.get_for_model(Reference)

    ReferenceAttachment.objects.create(
        content_type=content_type,
        object_id=object_id,
        reference=reference,
    )

    with pytest.raises(IntegrityError):
        ReferenceAttachment.objects.create(
            content_type=content_type,
            object_id=object_id,
            reference=reference,
        )

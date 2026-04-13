"""Tests for staged reference attachment helper APIs."""

from __future__ import annotations

import pytest

from apps.links.models import Reference, ReferenceAttachment
from apps.links.reference_utils import (
    get_attached_references,
    get_primary_reference,
    mirror_legacy_reference_attachment,
)
from apps.terms.models import Term

pytestmark = pytest.mark.django_db


def test_term_save_mirrors_legacy_reference_to_attachment_slot() -> None:
    reference = Reference.objects.create(
        alt_text="Term reference",
        value="https://example.com/term",
    )
    term = Term.objects.create(
        title="Terms",
        slug="terms",
        reference=reference,
    )

    attachment = ReferenceAttachment.objects.get(
        object_id=str(term.pk),
        slot="term",
        is_primary=True,
    )

    assert attachment.reference_id == reference.pk


def test_get_primary_reference_prefers_attachment_and_falls_back_to_legacy() -> None:
    legacy_reference = Reference.objects.create(
        alt_text="Legacy",
        value="https://example.com/legacy",
    )
    attachment_reference = Reference.objects.create(
        alt_text="Attachment",
        value="https://example.com/attachment",
    )
    term = Term.objects.create(
        title="Reference order",
        slug="reference-order",
        reference=legacy_reference,
    )

    attachment = ReferenceAttachment.objects.get(
        object_id=str(term.pk),
        slot="term",
        is_primary=True,
    )
    attachment.reference = attachment_reference
    attachment.save(update_fields=["reference"])

    assert get_primary_reference(term) == attachment_reference

    ReferenceAttachment.objects.filter(pk=attachment.pk).delete()

    assert get_primary_reference(term) == legacy_reference
    assert get_attached_references(term) == [legacy_reference]


def test_mirror_legacy_reference_attachment_updates_existing_slot() -> None:
    first_reference = Reference.objects.create(
        alt_text="First",
        value="https://example.com/first",
    )
    second_reference = Reference.objects.create(
        alt_text="Second",
        value="https://example.com/second",
    )
    term = Term.objects.create(
        title="Mirror",
        slug="mirror",
        reference=first_reference,
    )

    term.reference = second_reference
    mirror_legacy_reference_attachment(term)

    attachment = ReferenceAttachment.objects.get(
        object_id=str(term.pk),
        slot="term",
        is_primary=True,
    )
    assert attachment.reference_id == second_reference.pk

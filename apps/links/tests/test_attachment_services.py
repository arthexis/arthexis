"""Tests for generic reference attachment services."""

from __future__ import annotations

import pytest

from apps.links.models import Reference, ReferenceAttachment
from apps.links.services import (
    attach_reference,
    list_references,
    resolve_objects_by_reference,
)


pytestmark = pytest.mark.django_db


def test_attach_reference_is_idempotent_for_same_object_and_reference() -> None:
    target = Reference.objects.create(
        alt_text="Target",
        value="https://example.com/target",
        method="link",
    )

    first = attach_reference(
        target,
        alt_text="Docs",
        value="https://example.com/docs",
        slot="default",
        primary=True,
        method="link",
    )
    second = attach_reference(
        target,
        alt_text="Docs",
        value="https://example.com/docs",
        slot="default",
        primary=True,
        method="link",
    )

    assert first.pk == second.pk
    assert Reference.objects.filter(
        alt_text="Docs",
        value="https://example.com/docs",
    ).count() == 1
    assert ReferenceAttachment.objects.filter(
        content_type=first.content_type,
        object_id=str(target.pk),
    ).count() == 1


def test_attach_reference_replaces_primary_reference_for_slot() -> None:
    target = Reference.objects.create(
        alt_text="Primary target",
        value="https://example.com/primary-target",
        method="link",
    )

    first = attach_reference(
        target,
        alt_text="A",
        value="https://example.com/a",
        slot="header",
        primary=True,
        method="link",
    )
    second = attach_reference(
        target,
        alt_text="B",
        value="https://example.com/b",
        slot="header",
        primary=True,
        method="link",
    )

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.is_primary is False
    assert second.is_primary is True


def test_list_and_resolve_reference_services_filter_as_expected() -> None:
    target = Reference.objects.create(
        alt_text="Resolve target",
        value="https://example.com/resolve-target",
        method="link",
    )

    valid = attach_reference(
        target,
        alt_text="Valid",
        value="https://example.com/valid",
        slot="default",
        method="link",
    )
    invalid = attach_reference(
        target,
        alt_text="Invalid",
        value="https://example.com/invalid",
        slot="default",
        method="link",
        validation_status=500,
    )

    listed_default = list_references(target)
    listed_all = list_references(target, include_invalid=True)

    assert [ref.pk for ref in listed_default] == [valid.reference_id]
    assert sorted(ref.pk for ref in listed_all) == sorted(
        [valid.reference_id, invalid.reference_id]
    )

    resolved = resolve_objects_by_reference(
        Reference,
        value="https://example.com/valid",
        slot="default",
    )
    assert list(resolved.values_list("pk", flat=True)) == [target.pk]

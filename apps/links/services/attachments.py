"""Helpers for attaching and resolving generic link references."""

from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q

from apps.links.models import Reference, ReferenceAttachment


@transaction.atomic
def attach_reference(
    obj,
    *,
    alt_text,
    value,
    slot="default",
    primary=False,
    **ref_kwargs,
):
    """Create or update a reference and attach it to ``obj`` safely."""

    if obj.pk is None:
        raise ValueError("Cannot attach a reference to an unsaved object.")

    normalized_slot = (slot or "default").strip() or "default"
    reference, _ = Reference.objects.update_or_create(
        alt_text=alt_text,
        value=value,
        defaults=ref_kwargs,
    )
    content_type = ContentType.objects.get_for_model(
        obj,
        for_concrete_model=False,
    )
    if primary:
        ReferenceAttachment.objects.filter(
            content_type=content_type,
            object_id=str(obj.pk),
            is_primary=True,
            slot=normalized_slot,
        ).exclude(reference=reference).update(is_primary=False)

    attachment, _ = ReferenceAttachment.objects.update_or_create(
        content_type=content_type,
        object_id=str(obj.pk),
        reference=reference,
        defaults={
            "is_primary": primary,
            "slot": normalized_slot,
        },
    )

    return attachment


def list_references(obj, slot=None, include_invalid=False):
    """Return attached references for ``obj`` with optional filtering."""

    if obj.pk is None:
        return []

    content_type = ContentType.objects.get_for_model(
        obj,
        for_concrete_model=False,
    )
    attachments = ReferenceAttachment.objects.filter(
        content_type=content_type,
        object_id=str(obj.pk),
    ).select_related("reference")
    if slot is not None:
        attachments = attachments.filter(slot=slot)
    if not include_invalid:
        attachments = attachments.filter(
            Q(reference__validation_status__isnull=True)
            | Q(reference__validation_status__gte=200, reference__validation_status__lt=400)
        )

    return [attachment.reference for attachment in attachments]


def resolve_objects_by_reference(model_cls, *, value=None, alt_text=None, slot=None):
    """Resolve model instances linked by attached reference fields."""

    if value is None and alt_text is None:
        raise ValueError("Provide at least one of value or alt_text.")

    content_type = ContentType.objects.get_for_model(
        model_cls,
        for_concrete_model=False,
    )
    attachments = ReferenceAttachment.objects.filter(content_type=content_type)
    if slot is not None:
        attachments = attachments.filter(slot=slot)
    if alt_text is not None:
        attachments = attachments.filter(reference__alt_text=alt_text)
    if value is not None:
        attachments = attachments.filter(reference__value=value)

    object_ids = attachments.values_list("object_id", flat=True).distinct()
    pk_values = [model_cls._meta.pk.to_python(object_id) for object_id in object_ids]

    return model_cls._default_manager.filter(pk__in=pk_values)

from __future__ import annotations

from typing import Any

from django.contrib.contenttypes.models import ContentType

from .models import Discovery, DiscoveryItem


def start_discovery(
    action_label: str,
    request,
    *,
    model=None,
    metadata: dict[str, Any] | None = None,
) -> Discovery | None:
    user = getattr(request, "user", None)
    initiated_by = user if getattr(user, "is_authenticated", False) else None
    app_label = model._meta.app_label if model else ""
    model_name = model._meta.model_name if model else ""
    return Discovery.objects.create(
        action_label=str(action_label),
        app_label=app_label,
        model_name=model_name,
        initiated_by=initiated_by,
        metadata=metadata or None,
    )


def record_discovery_item(
    discovery: Discovery,
    *,
    obj=None,
    label: str = "",
    created: bool = False,
    overwritten: bool = False,
    data: dict[str, Any] | None = None,
) -> DiscoveryItem:
    content_type = None
    object_id = ""
    if obj is not None:
        content_type = ContentType.objects.get_for_model(obj, for_concrete_model=False)
        object_id = str(getattr(obj, "pk", "") or "")
        if not label:
            label = str(obj)
    return DiscoveryItem.objects.create(
        discovery=discovery,
        content_type=content_type,
        object_id=object_id,
        label=label or "",
        was_created=created,
        was_overwritten=overwritten,
        data=data or None,
    )

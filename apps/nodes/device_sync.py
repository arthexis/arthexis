from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Iterable, TypeVar

from django.db import models

if TYPE_CHECKING:
    from apps.nodes.models import Node

ModelType = TypeVar("ModelType", bound=models.Model)
DetectedType = TypeVar("DetectedType")


def sync_detected_devices(
    *,
    model_cls: type[ModelType],
    node: "Node",
    detected: Iterable[DetectedType],
    identifier_getter: Callable[[DetectedType], str],
    defaults_getter: Callable[[DetectedType], dict[str, object]],
    identifier_field: str = "identifier",
) -> tuple[int, int]:
    """Create, update, and remove device rows for ``node`` based on detection."""

    detected_devices = list(detected)
    existing = {
        getattr(device, identifier_field): device
        for device in model_cls.objects.filter(node=node)
    }
    seen: set[str] = set()
    created = 0
    updated = 0

    for device in detected_devices:
        identifier = identifier_getter(device)
        seen.add(identifier)
        obj = existing.get(identifier)
        defaults = defaults_getter(device)
        if obj is None:
            create_kwargs = {identifier_field: identifier, **defaults}
            model_cls.objects.create(node=node, **create_kwargs)
            created += 1
        else:
            update_fields: list[str] = []
            for field, value in defaults.items():
                if getattr(obj, field) != value:
                    setattr(obj, field, value)
                    update_fields.append(field)
            if update_fields:
                obj.save(update_fields=update_fields)
                updated += 1

    model_cls.objects.filter(node=node).exclude(
        **{f"{identifier_field}__in": seen}
    ).delete()

    return created, updated

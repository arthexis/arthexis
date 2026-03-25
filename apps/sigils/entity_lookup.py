"""ORM-backed helpers for entity sigil resolution."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from functools import lru_cache
from typing import Optional

from django.core.exceptions import FieldDoesNotExist, FieldError
from django.db import models
from django.db.models import Count, Max, Min, Sum


@lru_cache(maxsize=256)
def model_field_map(model: type[models.Model]) -> dict[str, models.Field]:
    """Return a case-insensitive mapping of model field names to fields."""
    return {field.name.lower(): field for field in model._meta.fields}


@lru_cache(maxsize=256)
def unique_char_fields(model: type[models.Model]) -> tuple[str, ...]:
    """Return unique character field names for fallback entity lookups."""
    return tuple(
        field.name
        for field in model._meta.fields
        if field.unique and isinstance(field, models.CharField)
    )


def parse_aggregate_request(
    filter_field: str | None,
    instance_id: str | None,
    normalized_key: str | None,
) -> tuple[str | None, str | None]:
    """Parse entity aggregate syntax expressed as ``target:function``."""
    if filter_field or instance_id is None or normalized_key is not None or ":" not in instance_id:
        return None, None
    aggregate_target, aggregate_func = instance_id.split(":", 1)
    return aggregate_target, (aggregate_func or "total").replace("-", "_").lower()


def resolve_entity_lookup(
    model: type[models.Model] | None,
    filter_field: str | None,
    instance_id: str | None,
    current: models.Model | None,
) -> tuple[models.Model | None, bool]:
    """Resolve the target entity instance from explicit selectors or ambient context."""
    instance = None
    invalid_lookup = False
    if model is None:
        return None, False

    if instance_id is not None:
        if filter_field:
            field_name = filter_field.lower()
            try:
                field_obj = model._meta.get_field(field_name)
            except FieldDoesNotExist:
                invalid_lookup = True
                field_obj = None
            if field_obj and isinstance(field_obj, models.CharField):
                lookup = {f"{field_name}__iexact": instance_id}
            else:
                lookup = {field_name: instance_id}
            try:
                instance = model.objects.filter(**lookup).first()
            except (FieldError, TypeError, ValueError):
                invalid_lookup = True
                instance = None
        else:
            try:
                instance = model.objects.filter(pk=instance_id).first()
            except (TypeError, ValueError):
                instance = None

    if instance is None and instance_id is not None and not filter_field:
        for field_name in unique_char_fields(model):
            instance = model.objects.filter(**{f"{field_name}__iexact": instance_id}).first()
            if instance:
                break

    if instance is None and instance_id is None and current and isinstance(current, model):
        instance = current
    return instance, invalid_lookup


def _coerce_numeric(value):
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _aggregate_values(values: list[float], func: str) -> str:
    if func == "count":
        return str(len(values))
    if not values:
        return ""
    if func == "min":
        return str(min(values))
    if func == "max":
        return str(max(values))
    return str(sum(values))


def resolve_entity_aggregate(
    model: type[models.Model],
    aggregate_target: str | None,
    aggregate_func: str | None,
    param_args: list[str],
    call_attribute: Callable[[object, str, list[str]], tuple[bool, object]],
    failed_resolution: Callable[[str], str],
    original_token: str,
) -> str | None:
    """Resolve aggregate sigils for entity models."""
    aggregate_candidates = {"total", "count", "min", "max"}
    if aggregate_func not in aggregate_candidates:
        return None

    qs = model.objects.all()
    target_name = (aggregate_target or "").replace("-", "_")
    if aggregate_func == "count" and not target_name:
        return str(qs.count())

    field = model_field_map(model).get(target_name.lower()) if target_name else None
    if field and aggregate_func in {"total", "min", "max", "count"}:
        aggregation_map = {
            "count": Count,
            "max": Max,
            "min": Min,
            "total": Sum,
        }
        agg_class = aggregation_map.get(aggregate_func)
        if agg_class:
            result = qs.aggregate(value=agg_class(field.attname)).get("value")
            return "" if result is None else str(result)

    values: list[float] = []
    for obj in qs:
        source = None
        if target_name:
            if field:
                source = getattr(obj, field.attname)
            else:
                found, source = call_attribute(obj, target_name, param_args)
                if not found:
                    continue
        if source is None:
            continue
        numeric = _coerce_numeric(source)
        if numeric is not None:
            values.append(numeric)

    aggregated = _aggregate_values(values, aggregate_func)
    return aggregated if aggregated is not None else failed_resolution(original_token)

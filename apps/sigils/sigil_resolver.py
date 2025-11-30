import json
import logging
import os
from decimal import Decimal
from typing import Iterable, Optional

from django.conf import settings
from django.core import serializers
from django.db import models

from .models import SigilRoot
from .sigil_context import get_context
from .system import get_system_sigil_values, resolve_system_namespace_value

logger = logging.getLogger(__name__)


def _first_instance(model: type[models.Model]) -> Optional[models.Model]:
    qs = model.objects
    ordering = list(getattr(model._meta, "ordering", []))
    if ordering:
        qs = qs.order_by(*ordering)
    else:
        qs = qs.order_by("?")
    return qs.first()


def _failed_resolution(token: str) -> str:
    return f"[{token}]"


def _normalize_name(name: str) -> str:
    return name.replace("-", "_")


def _candidate_names(name: str) -> list[str]:
    normalized = _normalize_name(name)
    return [
        name,
        normalized,
        normalized.lower(),
        normalized.upper(),
    ]


def _stringify_value(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if value is None:
        return ""
    return str(value)


def _coerce_numeric(value):
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    try:
        return float(value)
    except Exception:
        return None


def _call_attribute(obj, name: str, args: list[str]):
    for candidate in _candidate_names(name):
        if not hasattr(obj, candidate):
            continue
        attr = getattr(obj, candidate)
        if callable(attr):
            try:
                return True, attr(*args)
            except TypeError:
                return True, None
        return True, attr
    return False, None


def _aggregate_values(values: Iterable[float], func: str) -> Optional[str]:
    collected = [v for v in values if v is not None]
    if func == "count":
        return str(len(collected))
    if not collected:
        return ""
    if func == "min":
        return str(min(collected))
    if func == "max":
        return str(max(collected))
    # default to total
    return str(sum(collected))


def _resolve_token(token: str, current: Optional[models.Model] = None) -> str:
    original_token = token
    i = 0
    n = len(token)
    root_name = ""
    while i < n and token[i] not in ":=.":
        root_name += token[i]
        i += 1
    if not root_name:
        return _failed_resolution(original_token)
    filter_field = None
    if i < n and token[i] == ":":
        i += 1
        field = ""
        while i < n and token[i] != "=":
            field += token[i]
            i += 1
        if i == n:
            return _failed_resolution(original_token)
        filter_field = field.replace("-", "_")
    instance_id = None
    if i < n and token[i] == "=":
        i += 1
        start = i
        depth = 0
        while i < n:
            ch = token[i]
            if ch == "[":
                depth += 1
            elif ch == "]" and depth:
                depth -= 1
            elif ch == "." and depth == 0:
                break
            i += 1
        instance_id = token[start:i]
    key = None
    if i < n and token[i] == ".":
        i += 1
        start = i
        while i < n and token[i] != "=":
            i += 1
        key = token[start:i]
    param = None
    if i < n and token[i] == "=":
        param = token[i + 1 :]
    normalized_root = _normalize_name(root_name)
    lookup_root = normalized_root.upper()
    raw_key = key
    normalized_key = None
    key_upper = None
    key_lower = None
    if key:
        normalized_key = _normalize_name(key)
        key_upper = normalized_key.upper()
        key_lower = normalized_key.lower()
    param_args: list[str] = []
    if param:
        param = resolve_sigils(param, current)
        if param:
            param_args = param.split(",")
    if instance_id:
        instance_id = resolve_sigils(instance_id, current)
    try:
        root = SigilRoot.objects.get(prefix__iexact=lookup_root)
    except SigilRoot.DoesNotExist:
        logger.warning("Unknown sigil root [%s]", lookup_root)
        return _failed_resolution(original_token)
    except Exception:
        logger.exception(
            "Error resolving sigil [%s.%s]",
            lookup_root,
            key_upper or normalized_key or raw_key,
        )
        return _failed_resolution(original_token)

    try:
        if root.context_type == SigilRoot.Context.CONFIG:
            if not normalized_key:
                return ""
            if root.prefix.upper() == "ENV":
                candidates = []
                if raw_key:
                    candidates.append(raw_key.replace("-", "_"))
                if normalized_key:
                    candidates.append(normalized_key)
                if key_upper:
                    candidates.append(key_upper)
                if key_lower:
                    candidates.append(key_lower)
                seen_candidates: set[str] = set()
                for candidate in candidates:
                    if not candidate or candidate in seen_candidates:
                        continue
                    seen_candidates.add(candidate)
                    val = os.environ.get(candidate)
                    if val is not None:
                        return val
                logger.warning(
                    "Missing environment variable for sigil [ENV.%s]",
                    key_upper or normalized_key or raw_key or "",
                )
                return _failed_resolution(original_token)
            if root.prefix.upper() == "CONF":
                for candidate in [normalized_key, key_upper, key_lower]:
                    if not candidate:
                        continue
                    sentinel = object()
                    value = getattr(settings, candidate, sentinel)
                    if value is not sentinel:
                        return str(value)
                return ""
            if root.prefix.upper() == "SYS":
                values = get_system_sigil_values()
                candidates = {
                    key_upper,
                    normalized_key.upper() if normalized_key else None,
                    (raw_key or "").upper(),
                }
                for candidate in candidates:
                    if not candidate:
                        continue
                    if candidate in values:
                        return values[candidate]
                    resolved = resolve_system_namespace_value(candidate)
                    if resolved is not None:
                        return resolved
                logger.warning(
                    "Missing system information for sigil [SYS.%s]",
                    key_upper or normalized_key or raw_key or "",
                )
                return _failed_resolution(original_token)
        elif root.context_type == SigilRoot.Context.ENTITY:
            model = root.content_type.model_class() if root.content_type else None
            instance = None
            aggregate_target = None
            aggregate_func = None
            if (
                not filter_field
                and instance_id is not None
                and ":" in instance_id
                and normalized_key is None
            ):
                aggregate_target, aggregate_func = instance_id.split(":", 1)
                aggregate_func = _normalize_name(aggregate_func or "total").lower()
            manager_method_name = None
            if not filter_field and normalized_key is None:
                manager_method_name = instance_id if instance_id else None
            if model:
                if instance_id:
                    try:
                        if filter_field:
                            field_name = filter_field.lower()
                            try:
                                field_obj = model._meta.get_field(field_name)
                            except Exception:
                                field_obj = None
                            lookup: dict[str, str] = {}
                            if field_obj and isinstance(field_obj, models.CharField):
                                lookup = {f"{field_name}__iexact": instance_id}
                            else:
                                lookup = {field_name: instance_id}
                            instance = model.objects.filter(**lookup).first()
                        else:
                            instance = model.objects.filter(pk=instance_id).first()
                    except Exception:
                        instance = None
                    if instance is None and not filter_field:
                        for field in model._meta.fields:
                            if field.unique and isinstance(field, models.CharField):
                                instance = model.objects.filter(
                                    **{f"{field.name}__iexact": instance_id}
                                ).first()
                                if instance:
                                    break
                elif current and isinstance(current, model):
                    instance = current
                else:
                    ctx = get_context()
                    inst_pk = ctx.get(model)
                    if inst_pk is not None:
                        instance = model.objects.filter(pk=inst_pk).first()
                    if instance is None:
                        instance = _first_instance(model)
            if instance:
                if normalized_key:
                    resolver = getattr(instance, "resolve_profile_field_value", None)
                    if callable(resolver):
                        try:
                            handled, custom_value = resolver(normalized_key or raw_key or "")
                        except TypeError:
                            handled = False
                            custom_value = None
                        if handled:
                            return _stringify_value(custom_value)
                    field = next(
                        (
                            f
                            for f in model._meta.fields
                            if f.name.lower() == (key_lower or "")
                        ),
                        None,
                    )
                    if field:
                        val = getattr(instance, field.attname)
                        return _stringify_value(val)
                    found, attr_val = _call_attribute(
                        instance, normalized_key or raw_key or "", param_args
                    )
                    if found:
                        return _stringify_value(attr_val)
                    return _failed_resolution(original_token)
                return serializers.serialize("json", [instance])
            if not filter_field and normalized_key is None and model:
                aggregate_candidates = {"total", "count", "min", "max"}
                if aggregate_func in aggregate_candidates:
                    qs = model.objects.all()
                    target_name = _normalize_name(aggregate_target or "")
                    if aggregate_func == "count" and not target_name:
                        return str(qs.count())
                    values: list[float] = []
                    for obj in qs:
                        source = None
                        if target_name:
                            field = next(
                                (
                                    f
                                    for f in model._meta.fields
                                    if f.name.lower() == target_name.lower()
                                ),
                                None,
                            )
                            if field:
                                source = getattr(obj, field.attname)
                            else:
                                found, source = _call_attribute(
                                    obj, target_name, param_args
                                )
                                if not found:
                                    continue
                        if source is None:
                            continue
                        numeric = _coerce_numeric(source)
                        if numeric is not None:
                            values.append(numeric)
                    aggregated = _aggregate_values(values, aggregate_func)
                    return aggregated if aggregated is not None else _failed_resolution(original_token)
                if manager_method_name:
                    found, manager_val = _call_attribute(
                        model.objects, manager_method_name, param_args
                    )
                    if found:
                        if isinstance(manager_val, models.QuerySet):
                            return serializers.serialize("json", manager_val)
                        if isinstance(manager_val, models.Model):
                            return serializers.serialize("json", [manager_val])
                        return _stringify_value(manager_val)
        return _failed_resolution(original_token)
    except Exception:
        logger.exception(
            "Error resolving sigil [%s.%s]",
            lookup_root,
            key_upper or normalized_key or raw_key,
        )
        return _failed_resolution(original_token)


def resolve_sigils(text: str, current: Optional[models.Model] = None) -> str:
    result = ""
    i = 0
    while i < len(text):
        if text[i] == "[":
            depth = 1
            j = i + 1
            while j < len(text) and depth:
                if text[j] == "[":
                    depth += 1
                elif text[j] == "]":
                    depth -= 1
                j += 1
            if depth:
                result += text[i]
                i += 1
                continue
            token = text[i + 1 : j - 1]
            result += _resolve_token(token, current)
            i = j
        else:
            result += text[i]
            i += 1
    return result


def resolve_sigil(sigil: str, current: Optional[models.Model] = None) -> str:
    return resolve_sigils(sigil, current)

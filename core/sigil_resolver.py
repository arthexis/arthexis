import logging
import os
from typing import Optional

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.db import models

from .sigil_context import get_context
from .system import get_system_sigil_values, resolve_system_namespace_value

logger = logging.getLogger("core.entity")


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
    normalized_root = root_name.replace("-", "_")
    lookup_root = normalized_root.upper()
    raw_key = key
    normalized_key = None
    key_upper = None
    key_lower = None
    if key:
        normalized_key = key.replace("-", "_")
        key_upper = normalized_key.upper()
        key_lower = normalized_key.lower()
    if param:
        param = resolve_sigils(param, current)
    if instance_id:
        instance_id = resolve_sigils(instance_id, current)
    SigilRoot = apps.get_model("core", "SigilRoot")
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
                        return "" if val is None else str(val)
                    return _failed_resolution(original_token)
                return serializers.serialize("json", [instance])
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

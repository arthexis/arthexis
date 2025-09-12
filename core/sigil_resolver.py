import logging
import os
from io import StringIO
from typing import Optional

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.management import call_command
from django.db import models

from .sigil_context import get_context

logger = logging.getLogger("core.entity")


def _first_instance(model: type[models.Model]) -> Optional[models.Model]:
    qs = model.objects
    ordering = list(getattr(model._meta, "ordering", []))
    if ordering:
        qs = qs.order_by(*ordering)
    else:
        qs = qs.order_by("?")
    return qs.first()


def _resolve_token(token: str, current: Optional[models.Model] = None) -> str:
    i = 0
    n = len(token)
    root_name = ""
    while i < n and token[i] not in ":=.":
        root_name += token[i]
        i += 1
    if not root_name:
        return f"[{token}]"
    filter_field = None
    if i < n and token[i] == ":":
        i += 1
        field = ""
        while i < n and token[i] != "=":
            field += token[i]
            i += 1
        if i == n:
            return f"[{token}]"
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
    root_name = root_name.replace("-", "_").upper()
    if key:
        key = key.replace("-", "_").upper()
    if param:
        param = resolve_sigils(param, current)
    if instance_id:
        instance_id = resolve_sigils(instance_id, current)
    SigilRoot = apps.get_model("core", "SigilRoot")
    try:
        root = SigilRoot.objects.get(prefix__iexact=root_name)
        if root.context_type == SigilRoot.Context.CONFIG:
            if not key:
                return ""
            if root.prefix.upper() == "ENV":
                val = os.environ.get(key.upper())
                if val is None:
                    logger.warning(
                        "Missing environment variable for sigil [ENV.%s]", key.upper()
                    )
                    return f"[{token}]"
                return val
            if root.prefix.upper() == "SYS":
                return str(getattr(settings, key.upper(), ""))
            if root.prefix.upper() == "CMD":
                out = StringIO()
                args: list[str] = []
                if param:
                    args.append(param)
                call_command(key.lower(), *args, stdout=out)
                return out.getvalue().strip()
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
                if key:
                    field = next(
                        (
                            f
                            for f in model._meta.fields
                            if f.name.lower() == key.lower()
                        ),
                        None,
                    )
                    if field:
                        val = getattr(instance, field.attname)
                        return "" if val is None else str(val)
                    return f"[{token}]"
                return serializers.serialize("json", [instance])
        return f"[{token}]"
    except SigilRoot.DoesNotExist:
        logger.warning("Unknown sigil root [%s]", root_name)
    except Exception:
        logger.exception("Error resolving sigil [%s.%s]", root_name, key)
    return f"[{token}]"


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

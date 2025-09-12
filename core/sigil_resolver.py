import logging
import os
import re
from io import StringIO
from typing import Optional

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.management import call_command
from django.db import models

from .sigil_context import get_context

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(
    r"^([A-Za-z0-9_-]+)(?:=([A-Za-z0-9_-]+))?(?:\.([A-Za-z0-9_-]+)(?:=(.*))?)?$",
    re.IGNORECASE,
)


def _first_instance(model: type[models.Model]) -> Optional[models.Model]:
    qs = model.objects
    ordering = list(getattr(model._meta, "ordering", []))
    if ordering:
        qs = qs.order_by(*ordering)
    else:
        qs = qs.order_by("?")
    return qs.first()


def _resolve_token(token: str, current: Optional[models.Model] = None) -> str:
    match = TOKEN_RE.fullmatch(token)
    if not match:
        return f"[{token}]"
    root_name, instance_id, key, param = match.groups()
    root_name = root_name.replace("-", "_").upper()
    if key:
        key = key.replace("-", "_").upper()
    if param:
        param = resolve_sigils(param, current)
    SigilRoot = apps.get_model("core", "SigilRoot")
    try:
        root = SigilRoot.objects.get(prefix__iexact=root_name)
        if root.context_type == SigilRoot.Context.CONFIG:
            if not key:
                return ""
            if root.prefix.upper() == "ENV":
                return os.environ.get(key.upper(), "")
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
                        instance = model.objects.filter(pk=instance_id).first()
                    except (ValueError, TypeError):
                        instance = None
                    if instance is None:
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

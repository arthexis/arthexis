from __future__ import annotations

import os
import re
from io import StringIO

from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.core import serializers
from django.core.management import call_command
from django.db import models
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.translation import gettext_lazy as _

from .fields import SigilAutoFieldMixin


def _generate_prefix(name: str, existing: set[str]) -> str:
    words = [w for w in re.findall(r"[A-Z][^A-Z]*", name) if w]
    base = "".join(word[0] for word in words).upper()
    candidate = base
    extra_index = 1
    full_name = "".join(words)
    while candidate.upper() in existing:
        if extra_index < len(full_name):
            candidate = base + full_name[extra_index].upper()
            extra_index += 1
        else:
            candidate = f"{base}{extra_index}"
            extra_index += 1
    return candidate


def generate_model_sigils(**kwargs) -> None:
    """Create SigilRoot entries for all models using unique prefixes."""
    SigilRoot = apps.get_model("core", "SigilRoot")
    for prefix in ["ENV", "SYS", "CMD"]:
        # Ensure built-in configuration roots exist without violating the
        # unique ``prefix`` constraint, even if older databases already have
        # entries with a different ``context_type``.
        SigilRoot.objects.update_or_create(
            prefix__iexact=prefix,
            defaults={"prefix": prefix, "context_type": SigilRoot.Context.CONFIG},
        )

    existing = {p.upper() for p in SigilRoot.objects.values_list("prefix", flat=True)}
    for model in apps.get_models():
        ct = ContentType.objects.get_for_model(model)
        if SigilRoot.objects.filter(content_type=ct).exists():
            continue
        prefix = _generate_prefix(model.__name__, existing).upper()
        SigilRoot.objects.create(
            prefix=prefix,
            context_type=SigilRoot.Context.ENTITY,
            content_type=ct,
        )
        existing.add(prefix.upper())


SIGIL_PATTERN = re.compile(
    r"\[([A-Za-z0-9_-]+)(?:=([A-Za-z0-9_-]+))?(?:\.([A-Za-z0-9_-]+)(?:=([^\]]+))?)?\]",
    re.IGNORECASE,
)


def _resolve_sigil(sigil: str) -> str:
    """Return resolved value for ``sigil`` or empty string."""
    SigilRoot = apps.get_model("core", "SigilRoot")
    match = SIGIL_PATTERN.fullmatch(sigil)
    if not match:
        return ""
    root_name, instance_id, key, param = match.groups()
    root_name = root_name.replace("-", "_").upper()
    if key:
        key = key.replace("-", "_").upper()
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
                else:
                    instance = model.objects.order_by("?").first()
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
                else:
                    return serializers.serialize("json", [instance])
        return ""
    except Exception:
        return ""


def resolve_sigils_in_text(text: str) -> str:
    """Resolve all sigils within ``text`` and return the result."""
    return SIGIL_PATTERN.sub(lambda m: _resolve_sigil(m.group(0)), text)


def _sigil_builder_view(request):
    SigilRoot = apps.get_model("core", "SigilRoot")
    grouped: dict[str, dict[str, object]] = {}
    for root in SigilRoot.objects.filter(
        context_type=SigilRoot.Context.ENTITY
    ).select_related("content_type"):
        if not root.content_type:
            continue
        model = root.content_type.model_class()
        model_name = model._meta.object_name
        entry = grouped.setdefault(
            model_name,
            {
                "model": model_name,
                "fields": [f.name.upper() for f in model._meta.fields],
                "prefixes": [],
            },
        )
        entry["prefixes"].append(root.prefix.upper())
    roots = sorted(grouped.values(), key=lambda r: r["model"])
    for entry in roots:
        entry["prefixes"].sort()

    auto_fields = []
    for model in apps.get_models():
        for field in model._meta.fields:
            if isinstance(field, SigilAutoFieldMixin):
                auto_fields.append(
                    {"model": model._meta.object_name, "field": field.name.upper()}
                )

    sigils_text = ""
    resolved_text = ""
    if request.method == "POST":
        sigils_text = request.POST.get("sigils_text", "")
        upload = request.FILES.get("sigils_file")
        if upload:
            sigils_text = upload.read().decode("utf-8", errors="ignore")
        else:
            single = request.POST.get("sigil", "")
            if single:
                sigils_text = f"[{single}]" if not single.startswith("[") else single
        resolved_text = resolve_sigils_in_text(sigils_text) if sigils_text else ""

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Sigil Builder"),
            "sigil_roots": roots,
            "auto_fields": auto_fields,
            "sigils_text": sigils_text,
            "resolved_text": resolved_text,
        }
    )
    return TemplateResponse(request, "admin/sigil_builder.html", context)


def patch_admin_sigil_builder_view() -> None:
    """Add custom admin view for listing SigilRoots."""
    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path(
                "sigil-builder/",
                admin.site.admin_view(_sigil_builder_view),
                name="sigil_builder",
            ),
        ]
        return custom + urls

    admin.site.get_urls = get_urls

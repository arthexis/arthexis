from __future__ import annotations

import re

from django.apps import apps
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.translation import gettext_lazy as _

from .fields import SigilAutoFieldMixin
from .sigil_resolver import (
    resolve_sigils as resolve_sigils_in_text,
    resolve_sigil as _resolve_sigil,
)


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
        model_name = model._meta.object_name
        prefixes = grouped.get(model_name, {}).get("prefixes", [])
        for field in model._meta.fields:
            if isinstance(field, SigilAutoFieldMixin):
                auto_fields.append(
                    {
                        "model": model_name,
                        "roots": prefixes,
                        "field": field.name.upper(),
                    }
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

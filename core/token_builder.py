from __future__ import annotations

import re

from django.apps import apps
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.translation import gettext_lazy as _


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


def _token_builder_view(request):
    SigilRoot = apps.get_model("core", "SigilRoot")
    roots = []
    for root in SigilRoot.objects.filter(
        context_type=SigilRoot.Context.ENTITY
    ).select_related("content_type"):
        model = root.content_type.model_class()
        fields = [f.name.upper() for f in model._meta.fields]
        roots.append(
            {
                "prefix": root.prefix.upper(),
                "model": model._meta.object_name,
                "fields": fields,
            }
        )

    context = admin.site.each_context(request)
    context.update({"title": _("Token Builder"), "sigil_roots": roots})
    return TemplateResponse(request, "admin/token_builder.html", context)


def patch_admin_token_builder_view() -> None:
    """Add custom admin view for listing SigilRoots."""
    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path(
                "token-builder/",
                admin.site.admin_view(_token_builder_view),
                name="token_builder",
            ),
        ]
        return custom + urls

    admin.site.get_urls = get_urls

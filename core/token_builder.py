from __future__ import annotations

import os
import re
from io import StringIO

from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
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
    for prefix in ["ENV", "SYS", "CMD"]:
        SigilRoot.objects.get_or_create(
            prefix=prefix, context_type=SigilRoot.Context.CONFIG
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


TOKEN_PATTERN = re.compile(
    r"\[([A-Za-z0-9_-]+)(?:=([A-Za-z0-9_-]+))?\.([A-Za-z0-9_-]+)(?:=([^\]]+))?\]",
    re.IGNORECASE,
)


def _resolve_token(token: str) -> str:
    """Return resolved value for ``token`` or empty string."""
    SigilRoot = apps.get_model("core", "SigilRoot")
    match = TOKEN_PATTERN.fullmatch(token)
    if not match:
        return ""
    root_name, instance_id, key, param = match.groups()
    root_name = root_name.replace("-", "_").upper()
    key = key.replace("-", "_").upper()
    try:
        root = SigilRoot.objects.get(prefix__iexact=root_name)
        if root.context_type == SigilRoot.Context.CONFIG:
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
                    instance = model.objects.filter(pk=instance_id).first()
                else:
                    instance = model.objects.order_by("?").first()
            if instance:
                field = next(
                    (f for f in model._meta.fields if f.name.lower() == key.lower()),
                    None,
                )
                if field:
                    val = getattr(instance, field.attname)
                    return "" if val is None else str(val)
        return ""
    except Exception:
        return ""


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

    token = ""
    resolved = ""
    if request.method == "POST":
        token = request.POST.get("token", "").strip()
        if token and not token.startswith("["):
            token = f"[{token}]"
        resolved = _resolve_token(token) if token else ""

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Token Builder"),
            "sigil_roots": roots,
            "token": token,
            "resolved": resolved,
        }
    )
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

from __future__ import annotations


from django.apps import apps
from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from .fields import SigilAutoFieldMixin
from .sigil_resolver import (
    resolve_sigils as resolve_sigils_in_text,
    resolve_sigil as _resolve_sigil,
)


def generate_model_sigils(**kwargs) -> None:
    """Ensure built-in configuration SigilRoot entries exist."""
    SigilRoot = apps.get_model("core", "SigilRoot")
    for prefix in ["ENV", "CONF", "SYS"]:
        # Ensure built-in configuration roots exist without violating the
        # unique ``prefix`` constraint, even if older databases already have
        # entries with a different ``context_type``.
        root = SigilRoot.objects.filter(prefix__iexact=prefix).first()
        if root:
            root.prefix = prefix
            root.context_type = SigilRoot.Context.CONFIG
            root.save(update_fields=["prefix", "context_type"])
        else:
            SigilRoot.objects.create(
                prefix=prefix,
                context_type=SigilRoot.Context.CONFIG,
            )


def _sigil_builder_view(request):
    SigilRoot = apps.get_model("core", "SigilRoot")
    grouped: dict[str, dict[str, object]] = {}
    builtin_roots = [
        {
            "prefix": "ENV",
            "url": reverse("admin:environment"),
            "label": _("Environment"),
        },
        {
            "prefix": "CONF",
            "url": reverse("admin:config"),
            "label": _("Django Config"),
        },
        {
            "prefix": "SYS",
            "url": reverse("admin:system"),
            "label": _("System"),
        },
    ]
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
    seen = set()
    for model in apps.get_models():
        model_name = model._meta.object_name
        if model_name in seen:
            continue
        seen.add(model_name)
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
    show_sigils_input = True
    show_result = False
    if request.method == "POST":
        sigils_text = request.POST.get("sigils_text", "")
        source_text = sigils_text
        upload = request.FILES.get("sigils_file")
        if upload:
            source_text = upload.read().decode("utf-8", errors="ignore")
            show_sigils_input = False
        else:
            single = request.POST.get("sigil", "")
            if single:
                source_text = (
                    f"[{single}]" if not single.startswith("[") else single
                )
                sigils_text = source_text
        if source_text:
            resolved_text = resolve_sigils_in_text(source_text)
            show_result = True
        if upload:
            sigils_text = ""

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Sigil Builder"),
            "sigil_roots": roots,
            "builtin_roots": builtin_roots,
            "auto_fields": auto_fields,
            "sigils_text": sigils_text,
            "resolved_text": resolved_text,
            "show_sigils_input": show_sigils_input,
            "show_result": show_result,
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

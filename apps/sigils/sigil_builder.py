from __future__ import annotations

from django.apps import apps
from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from .builtin_policy import BUILTIN_SIGIL_POLICIES
from .fields import SigilAutoFieldMixin
from .loader import load_fixture_sigil_roots
from .models import SigilRoot
from .sigil_resolver import get_user_safe_sigil_actions, get_user_safe_sigil_roots
from .sigil_resolver import resolve_sigils as resolve_sigils_in_text

SUPPORTED_PIPELINE_ACTIONS = (
    "COUNT",
    "FIELD",
    "FILTER",
    "GET",
    "MAX",
    "MIN",
    "SUM",
    "TOTAL",
)


class SigilBuilderResponse(TemplateResponse):
    @property
    def context(self):
        return self.context_data

    @context.setter
    def context(self, value):
        if value is not None:
            self.context_data = value


def generate_model_sigils(**kwargs) -> None:
    """Ensure built-in configuration SigilRoot entries exist."""
    for prefix, policy in BUILTIN_SIGIL_POLICIES.items():
        # Ensure built-in configuration roots exist without violating the
        # unique ``prefix`` constraint, even if older databases already have
        # entries with a different ``context_type`` or are soft deleted.
        root = SigilRoot.all_objects.filter(prefix__iexact=prefix).first()
        if root:
            root.prefix = prefix
            root.context_type = policy["context_type"]
            root.is_user_safe = policy["is_user_safe"]
            root.is_deleted = False
            root.save(update_fields=["prefix", "context_type", "is_user_safe", "is_deleted"])
        else:
            SigilRoot.objects.create(
                prefix=prefix,
                context_type=policy["context_type"],
                is_user_safe=policy["is_user_safe"],
            )


def _sigil_builder_view(request):
    if not SigilRoot.objects.filter(
        context_type=SigilRoot.Context.ENTITY
    ).exists():
        load_fixture_sigil_roots()

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
        {
            "prefix": "REQ",
            "url": "",
            "label": _("Request"),
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
        canonical_root = entry["prefixes"][0] if entry["prefixes"] else "ROOT"
        canonical_field = entry["fields"][0] if entry["fields"] else "FIELD"
        entry["example"] = f"[{canonical_root}:|FIELD:{canonical_field}]"

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
                        "example": (
                            f"[{prefixes[0]}:|FIELD:{field.name.upper()}]"
                            if prefixes
                            else ""
                        ),
                    }
                )

    expression_examples = [
        {
            "context": "admin",
            "root": "CP",
            "action": "FIELD",
            "expression": "[CP:hostname:SIM-CP-1|FIELD:PUBLIC_ENDPOINT]",
            "legacy_expression": "[CP:hostname=SIM-CP-1.public_endpoint]",
            "description": _("OCPP charger lookup by hostname."),
        },
        {
            "context": "admin",
            "root": "SESS",
            "action": "COUNT",
            "expression": "[SESS:status:ACTIVE|COUNT:ID]",
            "legacy_expression": "[SESS:status=ACTIVE.id=count]",
            "description": _("Count active charging sessions."),
        },
        {
            "context": "user-safe",
            "root": "CP",
            "action": "FILTER",
            "expression": "[CP:owner__name:__OWNER_NAME__|FILTER:STATUS:AVAILABLE]",
            "legacy_expression": "[CP:owner__name=__OWNER_NAME__.status:AVAILABLE]",
            "description": _("Ownership-aware charger filtering placeholder."),
        },
        {
            "context": "request",
            "root": "REQ",
            "action": "GET",
            "expression": "[REQ|GET:ID_TAG]",
            "legacy_expression": "[REQ.get=id_tag]",
            "description": _("Request metadata lookup from query params."),
        },
        {
            "context": "admin",
            "root": "SYS",
            "action": "GET",
            "expression": "[SYS|GET:VERSION]",
            "legacy_expression": "[SYS.VERSION]",
            "description": _("System metadata lookup for release/version details."),
        },
    ]
    example_roots = sorted({entry["root"] for entry in expression_examples})
    example_actions = sorted({entry["action"] for entry in expression_examples})
    example_contexts = sorted({entry["context"] for entry in expression_examples})
    policy_reference = [
        {
            "context": "admin",
            "roots": _("All registered SigilRoot prefixes (canonical uppercase)."),
            "actions": _("Any uppercase action token; recommended actions listed above."),
        },
        {
            "context": "user-safe",
            "roots": ", ".join(sorted(get_user_safe_sigil_roots())) or _("None"),
            "actions": ", ".join(sorted(get_user_safe_sigil_actions())) or _("None"),
        },
        {
            "context": "request",
            "roots": "REQ",
            "actions": "GET",
        },
    ]

    errors: list[str] = []
    sigils_text = ""
    resolved_text = ""
    show_sigils_input = True
    show_result = False
    if request.method == "POST":
        sigils_text = request.POST.get("sigils_text", "")
        source_text = sigils_text
        upload = request.FILES.get("sigils_file")
        if upload is not None:
            if not hasattr(upload, "read"):
                errors.append(_("Uploaded file could not be processed."))
            elif getattr(upload, "size", None) in (None, 0):
                errors.append(_("Uploaded file is empty."))
            else:
                try:
                    source_text = upload.read().decode("utf-8", errors="ignore")
                    show_sigils_input = False
                except Exception:
                    errors.append(_("Unable to read uploaded file."))
        else:
            single = request.POST.get("sigil", "")
            if single:
                source_text = (
                    f"[{single}]" if not single.startswith("[") else single
                )
                sigils_text = source_text
        if source_text and not errors:
            resolved_text = resolve_sigils_in_text(source_text)
            show_result = True
        if upload is not None and not errors:
            sigils_text = ""

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Sigil Builder"),
            "sigil_roots": roots,
            "builtin_roots": builtin_roots,
            "auto_fields": auto_fields,
            "expression_examples": expression_examples,
            "example_roots": example_roots,
            "example_actions": example_actions,
            "example_contexts": example_contexts,
            "policy_reference": policy_reference,
            "supported_pipeline_actions": SUPPORTED_PIPELINE_ACTIONS,
            "sigils_text": sigils_text,
            "resolved_text": resolved_text,
            "errors": errors,
            "show_sigils_input": show_sigils_input,
            "show_result": show_result,
        }
    )
    response = SigilBuilderResponse(request, "admin/sigil_builder.html", context)
    response.render()
    return response


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

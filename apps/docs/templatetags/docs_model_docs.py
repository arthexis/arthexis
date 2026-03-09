"""Template tags for model/documentation cross-links."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.template import Library

from apps.docs.models import ModelDocumentation

register = Library()


@register.simple_tag
def model_documentation_links(opts) -> list[ModelDocumentation]:
    """Return documentation rows linked to the given model metadata."""

    if opts is None:
        return []
    content_type = ContentType.objects.get_for_model(opts.model, for_concrete_model=False)
    return list(
        ModelDocumentation.objects.filter(models=content_type)
        .order_by("title")
        .only("id", "title", "doc_path")
    )


@register.simple_tag
def linked_admin_model_links(document_path: str | None) -> list[dict[str, str]]:
    """Return admin changelist links for models linked to the given document path."""

    normalized = _normalize_document_path(document_path)
    if not normalized:
        return []
    record = (
        ModelDocumentation.objects.prefetch_related("models")
        .filter(doc_path=normalized)
        .first()
    )
    return record.linked_model_admin_urls if record else []


@register.filter
def document_url(record: ModelDocumentation) -> str:
    """Return the public docs URL for a model documentation record."""

    return record.document_url()


def _normalize_document_path(document_path: str | None) -> str | None:
    if not document_path:
        return None
    normalized = document_path.strip().replace("\\", "/").lstrip("/")
    if not normalized:
        return None

    base_dir = Path(settings.BASE_DIR).resolve()
    candidate = (base_dir / normalized).resolve(strict=False)
    for root_prefix in ("docs", "apps/docs"):
        root = (base_dir / root_prefix).resolve(strict=False)
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        return f"{root_prefix}/{relative.as_posix()}"

    if normalized.startswith(("docs/", "apps/docs/")):
        return normalized
    return None

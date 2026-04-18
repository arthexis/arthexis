from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import NoReverseMatch, reverse

from apps.core.entity import Entity

COOKBOOK_ROOT = Path(__file__).resolve().parent / "cookbooks"
DOC_FILE_EXTENSIONS = {".md", ".markdown"}


class Cookbook(Entity):
    slug = models.SlugField(max_length=150, unique=True)
    title = models.CharField(max_length=255)
    file_name = models.CharField(
        max_length=255, help_text="Relative path inside the cookbooks/ folder"
    )

    class Meta:
        ordering = ["title"]
        verbose_name = "Cookbook"
        verbose_name_plural = "Cookbooks"
        db_table = "docs_cookbook"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.title

    @property
    def path(self) -> Path:
        return (COOKBOOK_ROOT / self.file_name).resolve()

    def clean(self):
        super().clean()
        candidate = self.path
        try:
            candidate.relative_to(COOKBOOK_ROOT)
        except ValueError:
            raise ValidationError(
                {"file_name": "Cookbook files must be stored inside cookbooks/."}
            )
        if not candidate.is_file():
            raise ValidationError({"file_name": "Cookbook file does not exist."})


class ModelDocumentation(Entity):
    """Link local markdown documents to one or more admin models."""

    title = models.CharField(max_length=255)
    doc_path = models.CharField(
        max_length=255,
        unique=True,
        help_text=(
            "Relative documentation path (for example: docs/platform/overview.md or "
            "apps/docs/integrations/api.md)."
        ),
    )
    models = models.ManyToManyField(ContentType, related_name="documentation_links", blank=True)

    class Meta:
        ordering = ["title"]
        verbose_name = "Model documentation"
        verbose_name_plural = "Model documentation"
        db_table = "docs_model_documentation"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.title

    @classmethod
    def normalize_doc_path(cls, value: str) -> str:
        """Return a normalized relative doc path for lookups and validation."""

        normalized = (value or "").strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        normalized = normalized.lstrip("/")
        if not normalized or ".." in Path(normalized).parts:
            raise ValidationError({"doc_path": "Enter a valid relative document path."})
        return normalized

    def clean(self):
        super().clean()
        normalized = self.normalize_doc_path(self.doc_path)
        extension = Path(normalized).suffix.lower()
        if extension not in DOC_FILE_EXTENSIONS:
            raise ValidationError(
                {"doc_path": "Documentation path must point to a Markdown file."}
            )

        base_dir = Path(settings.BASE_DIR).resolve()
        candidate = (base_dir / normalized).resolve(strict=False)
        try:
            candidate.relative_to(base_dir)
        except ValueError as exc:
            raise ValidationError(
                {"doc_path": "Documentation path must remain inside the project root."}
            ) from exc
        if not candidate.is_file():
            raise ValidationError({"doc_path": "Documentation file does not exist."})
        self.doc_path = normalized

    def document_url(self) -> str:
        """Return the docs reader URL for this record's path."""

        if self.doc_path.startswith("apps/docs/"):
            relative = self.doc_path.removeprefix("apps/docs/")
            return reverse("docs:apps-docs-document", args=[relative])
        if self.doc_path.startswith("docs/"):
            relative = self.doc_path.removeprefix("docs/")
            return reverse("docs:docs-document", args=[relative])
        return reverse("docs:docs-document", args=[self.doc_path])

    @property
    def linked_model_admin_urls(self) -> list[dict[str, str]]:
        """Return changelist URLs for all linked models that can be reversed."""

        links: list[dict[str, str]] = []
        for content_type in self.models.order_by("app_label", "model"):
            try:
                url = reverse(
                    f"admin:{content_type.app_label}_{content_type.model}_changelist"
                )
            except NoReverseMatch:
                continue
            links.append(
                {
                    "label": f"{content_type.app_label}.{content_type.model}",
                    "url": url,
                }
            )
        return links


class DocumentIndex(Entity):
    """Classify and track documents for targeted course-style access."""

    ACCESS_AVAILABLE = "available"
    ACCESS_RECOMMENDED = "recommended"
    ACCESS_REQUIRED = "required"
    ACCESS_RESTRICTED = "restricted"
    ACCESS_CHOICES = (
        (ACCESS_AVAILABLE, "Available"),
        (ACCESS_RECOMMENDED, "Recommended"),
        (ACCESS_REQUIRED, "Required"),
        (ACCESS_RESTRICTED, "Restricted"),
    )

    title = models.CharField(max_length=255, blank=True)
    doc_path = models.CharField(
        max_length=255,
        unique=True,
        help_text=(
            "Relative documentation path (for example: docs/platform/overview.md or "
            "apps/docs/integrations/api.md)."
        ),
    )
    listable = models.BooleanField(
        default=True,
        help_text="Allow display in Indexed Documents even when no course group is assigned.",
    )

    class Meta:
        ordering = ["title", "doc_path"]
        verbose_name = "Document index"
        verbose_name_plural = "Document index"
        db_table = "docs_document_index"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.title or self.doc_path

    def clean(self):
        super().clean()
        normalized = ModelDocumentation.normalize_doc_path(self.doc_path)
        extension = Path(normalized).suffix.lower()
        if extension not in DOC_FILE_EXTENSIONS:
            raise ValidationError(
                {"doc_path": "Documentation path must point to a Markdown file."}
            )

        base_dir = Path(settings.BASE_DIR).resolve()
        candidate = (base_dir / normalized).resolve(strict=False)
        try:
            candidate.relative_to(base_dir)
        except ValueError as exc:
            raise ValidationError(
                {"doc_path": "Documentation path must remain inside the project root."}
            ) from exc
        if not candidate.is_file():
            raise ValidationError({"doc_path": "Documentation file does not exist."})
        self.doc_path = normalized
        if not self.title:
            self.title = Path(normalized).stem.replace("-", " ").replace("_", " ").title()

    def document_url(self) -> str:
        if self.doc_path.startswith("apps/docs/"):
            relative = self.doc_path.removeprefix("apps/docs/")
            return reverse("docs:apps-docs-document", args=[relative])
        if self.doc_path.startswith("docs/"):
            relative = self.doc_path.removeprefix("docs/")
            return reverse("docs:docs-document", args=[relative])
        return reverse("docs:docs-document", args=[self.doc_path])


class DocumentIndexAssignment(Entity):
    """Map a document to a security-group course with an access level."""

    document = models.ForeignKey(
        DocumentIndex,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    security_group = models.ForeignKey(
        "groups.SecurityGroup",
        on_delete=models.CASCADE,
        related_name="document_assignments",
    )
    access = models.CharField(max_length=16, choices=DocumentIndex.ACCESS_CHOICES)

    class Meta:
        ordering = ["security_group__name", "document__title", "document__doc_path"]
        verbose_name = "Document index assignment"
        verbose_name_plural = "Document index assignments"
        db_table = "docs_document_index_assignment"
        constraints = [
            models.UniqueConstraint(
                fields=["document", "security_group"],
                name="docs_unique_document_security_group_assignment",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.document} → {self.security_group} ({self.get_access_display()})"

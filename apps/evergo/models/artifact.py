"""Artifact model for Evergo customer attachments."""

from __future__ import annotations

from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import models


class EvergoArtifact(models.Model):
    """Uploaded customer artifact restricted to image and PDF formats."""

    ARTIFACT_TYPE_IMAGE = "image"
    ARTIFACT_TYPE_PDF = "pdf"
    ARTIFACT_TYPE_CHOICES = (
        (ARTIFACT_TYPE_IMAGE, "Image"),
        (ARTIFACT_TYPE_PDF, "PDF"),
    )

    customer = models.ForeignKey(
        "evergo.EvergoCustomer",
        on_delete=models.CASCADE,
        related_name="artifacts",
    )
    file = models.FileField(upload_to="evergo/artifacts/")
    artifact_type = models.CharField(max_length=16, choices=ARTIFACT_TYPE_CHOICES, editable=False)
    display_order = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Evergo Artifact"
        verbose_name_plural = "Evergo Artifacts"
        ordering = ("display_order", "created_at", "pk")

    def __str__(self) -> str:
        """Return a concise artifact label."""
        return f"{self.customer} · {Path(self.file.name).name}"

    @property
    def filename(self) -> str:
        """Return artifact filename for labels."""
        return Path(self.file.name).name

    @property
    def is_image(self) -> bool:
        """Return True when artifact is an image."""
        return self.artifact_type == self.ARTIFACT_TYPE_IMAGE

    @property
    def is_pdf(self) -> bool:
        """Return True when artifact is a PDF."""
        return self.artifact_type == self.ARTIFACT_TYPE_PDF

    def clean(self) -> None:
        """Validate supported file extensions and infer artifact type."""
        super().clean()
        if not self.file:
            return
        suffix = Path(self.file.name).suffix.lower()
        if suffix == ".pdf":
            self.artifact_type = self.ARTIFACT_TYPE_PDF
            return
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            self.artifact_type = self.ARTIFACT_TYPE_IMAGE
            return
        raise ValidationError({"file": "Only image files and PDFs are allowed."})

    def save(self, *args, **kwargs):
        """Enforce validation before persisting artifacts."""
        self.full_clean()
        super().save(*args, **kwargs)

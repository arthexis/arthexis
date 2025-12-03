from __future__ import annotations

import base64
from typing import Any

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class UserManual(Entity):
    class PdfOrientation(models.TextChoices):
        LANDSCAPE = "landscape", _("Landscape")
        PORTRAIT = "portrait", _("Portrait")

    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=200)
    description = models.CharField(max_length=200)
    languages = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Comma-separated 2-letter language codes",
    )
    content_html = models.TextField()
    content_pdf = models.TextField(help_text="Base64 encoded PDF")
    pdf_orientation = models.CharField(
        max_length=10,
        choices=PdfOrientation.choices,
        default=PdfOrientation.LANDSCAPE,
        help_text=_("Orientation used when rendering the PDF download."),
    )

    class Meta:
        db_table = "man_usermanual"
        verbose_name = "User Manual"
        verbose_name_plural = "User Manuals"

    def __str__(self):  # pragma: no cover - simple representation
        return self.title

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.slug,)

    def _read_pdf_content(self, value: Any) -> bytes | None:
        reader = getattr(value, "read", None)
        if callable(reader):
            data = reader()
            reset = getattr(value, "seek", None)
            if callable(reset):
                try:
                    reset(0)
                except Exception:  # pragma: no cover - best effort reset
                    pass
            return data
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value)
        return None

    def _ensure_pdf_is_base64(self) -> None:
        """Normalize ``content_pdf`` so stored values are base64 strings."""

        value = self.content_pdf
        if value in {None, ""}:
            self.content_pdf = "" if value is None else value
            return

        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("data:"):
                _, _, encoded = stripped.partition(",")
                self.content_pdf = encoded.strip()
                return

        data = self._read_pdf_content(value)
        if data is not None:
            self.content_pdf = base64.b64encode(data).decode("ascii")

    def save(self, *args, **kwargs):
        self._ensure_pdf_is_base64()
        super().save(*args, **kwargs)

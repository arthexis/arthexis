from __future__ import annotations

from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.entity import Entity

COOKBOOK_ROOT = Path(__file__).resolve().parent / "cookbooks"


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

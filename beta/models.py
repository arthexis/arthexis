from django.db import models
from django.utils.translation import gettext_lazy as _


class GameMaterial(models.Model):
    slug = models.SlugField(unique=True)
    image = models.TextField()
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["slug"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.slug


class GamePortal(models.Model):
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    entry_material = models.ForeignKey(
        GameMaterial, on_delete=models.SET_NULL, blank=True, null=True
    )

    class Meta:
        verbose_name = _("Game Portal")
        verbose_name_plural = _("Game Portals")
        ordering = ["title"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.title


class MaterialRegion(models.Model):
    material = models.ForeignKey(
        GameMaterial, related_name="regions", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=200)
    x = models.PositiveIntegerField()
    y = models.PositiveIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    target = models.ForeignKey(
        GameMaterial,
        related_name="incoming_regions",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.material.slug}: {self.name}"


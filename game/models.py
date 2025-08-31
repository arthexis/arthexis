import base64
import random
import uuid
from io import BytesIO

import requests
from PIL import Image, ImageFilter
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
        verbose_name = _("Game")
        verbose_name_plural = _("Games")
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


def create_random_material() -> GameMaterial:
    """Fetch a random image and create a material with two regions."""
    resp = requests.get("https://source.unsplash.com/random/800x600", timeout=10)
    img = Image.open(BytesIO(resp.content)).convert("RGB")
    width, height = img.size
    buf = BytesIO()
    img.save(buf, format="PNG")
    image_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    material = GameMaterial.objects.create(slug=uuid.uuid4().hex[:8], image=image_b64)
    edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
    edge_pixels = [
        (x, y)
        for y in range(height)
        for x in range(width)
        if edges.getpixel((x, y)) > 0
    ]
    if len(edge_pixels) < 2:
        edge_pixels = [
            (random.randrange(width), random.randrange(height))
            for _ in range(2)
        ]
    else:
        edge_pixels = random.sample(edge_pixels, 2)
    for idx, (x, y) in enumerate(edge_pixels, start=1):
        x0 = max(0, x - 50)
        y0 = max(0, y - 50)
        w = min(100, width - x0)
        h = min(100, height - y0)
        MaterialRegion.objects.create(
            material=material, name=f"Option {idx}", x=x0, y=y0, width=w, height=h
        )
    return material


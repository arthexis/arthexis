"""Domain models for Raspberry Pi image artifacts."""

from django.db import models


class RaspberryPiImageArtifact(models.Model):
    """Persist metadata for generated Raspberry Pi image artifacts."""

    name = models.CharField(max_length=120, unique=True)
    target = models.CharField(max_length=40, default="rpi-4b")
    base_image_uri = models.URLField()
    output_filename = models.CharField(max_length=255)
    output_path = models.CharField(max_length=500)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.BigIntegerField()
    download_uri = models.URLField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Raspberry Pi image artifact"
        verbose_name_plural = "Raspberry Pi image artifacts"

    def __str__(self) -> str:
        """Return a readable artifact name."""

        return f"{self.name} ({self.target})"

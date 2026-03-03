"""Models for tracking configured liboqs algorithm profiles."""

from django.db import models


class LiboqsProfile(models.Model):
    """Represents a configured liboqs algorithm profile in the platform."""

    slug = models.SlugField(max_length=64, unique=True)
    display_name = models.CharField(max_length=128)
    kem_algorithm = models.CharField(max_length=128)
    signature_algorithm = models.CharField(max_length=128, blank=True)
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("display_name",)
        verbose_name = "liboqs profile"
        verbose_name_plural = "liboqs profiles"

    def __str__(self) -> str:
        """Return a human-readable label for admin and logs."""

        return self.display_name

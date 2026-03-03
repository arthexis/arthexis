"""Models for liboqs algorithm capability metadata."""

from django.db import models


class OqsAlgorithm(models.Model):
    """Represents a liboqs algorithm exposed to the platform."""

    class AlgorithmType(models.TextChoices):
        """Supported liboqs algorithm families."""

        KEM = "kem", "KEM"
        SIGNATURE = "signature", "Signature"

    name = models.CharField(max_length=128, unique=True)
    algorithm_type = models.CharField(max_length=16, choices=AlgorithmType.choices)
    enabled = models.BooleanField(default=True)
    discovered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "OQS algorithm"
        verbose_name_plural = "OQS algorithms"

    def __str__(self) -> str:
        """Return a human-readable label for admin interfaces."""

        return self.name

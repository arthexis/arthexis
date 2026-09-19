from django.db import models


class SchemaGeneration(models.Model):
    """Records the database family that owns this data directory."""

    generation = models.PositiveSmallIntegerField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

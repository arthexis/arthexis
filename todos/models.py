"""Database models for the todos app."""

from django.db import models


class Todo(models.Model):
    """A simple task item extracted from code or created by users."""

    text = models.CharField(max_length=255)
    completed = models.BooleanField(default=False)
    file_path = models.CharField(max_length=255, blank=True)
    line_number = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = ("text", "file_path", "line_number")

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.text


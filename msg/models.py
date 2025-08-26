from django.db import models
from integrate.models import Entity


class Message(Entity):
    """System message that can be sent to LCD or GUI."""

    subject = models.CharField(max_length=32, blank=True)
    body = models.CharField(max_length=32)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.subject} {self.body}".strip()

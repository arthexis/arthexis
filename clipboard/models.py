from django.db import models


class Sample(models.Model):
    """Clipboard text captured with timestamp."""

    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.content[:50] if len(self.content) > 50 else self.content

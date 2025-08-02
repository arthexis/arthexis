from django.conf import settings
from django.db import models


class BskyAccount(models.Model):
    """Bluesky account linked to a user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bsky"
    )
    handle = models.CharField(max_length=255, unique=True)
    app_password = models.CharField(
        max_length=255, help_text="Bluesky app password for API access"
    )

    def __str__(self):  # pragma: no cover - simple representation
        return self.handle

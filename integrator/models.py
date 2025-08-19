from django.conf import settings
from django.db import models

from fernet_fields import EncryptedCharField
from utils.sigils import SigilCharField, SigilURLField


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


class OdooInstance(models.Model):
    """Connection details for an Odoo server."""

    name = models.CharField(max_length=100)
    url = SigilURLField()
    database = SigilCharField(max_length=100)
    username = SigilCharField(max_length=100)
    password = EncryptedCharField(max_length=100)

    def __str__(self) -> str:  # pragma: no cover - simple repr
        return self.name

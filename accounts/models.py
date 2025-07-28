from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user with optional RFID UID for card-based login."""

    rfid_uid = models.CharField(
        max_length=64,
        unique=True,
        blank=True,
        null=True,
        help_text="RFID card identifier",
    )

    def __str__(self):
        return self.username

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError


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

    def clean(self):
        super().clean()
        if self.rfid_uid and BlacklistedRFID.objects.filter(uid=self.rfid_uid).exists():
            raise ValidationError({"rfid_uid": "This RFID has been blacklisted."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class BlacklistedRFID(models.Model):
    """RFID identifiers that are permanently blocked."""

    uid = models.CharField(max_length=64, unique=True)
    added_on = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        User = get_user_model()
        try:
            user = User.objects.get(rfid_uid=self.uid)
            user.rfid_uid = None
            user.save(update_fields=["rfid_uid"])
        except User.DoesNotExist:
            pass

    def __str__(self):  # pragma: no cover - simple representation
        return self.uid

from django.conf import settings
from django.db import models

from apps.base.models import Entity


class UserPhoneNumber(Entity):
    """Store phone numbers associated with a user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="phone_numbers",
    )
    number = models.CharField(
        max_length=32,
        help_text="Contact phone number",
    )
    priority = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("priority", "id")
        verbose_name = "Phone Number"
        verbose_name_plural = "Phone Numbers"
        db_table = "core_userphonenumber"

    def __str__(self):  # pragma: no cover - simple representation
        return f"{self.number} ({self.priority})"

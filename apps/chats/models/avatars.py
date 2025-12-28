from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class ChatAvatar(Entity):
    """Represents an operator identity for handling chats."""

    name = models.CharField(max_length=150)
    photo = models.ImageField(upload_to="chats/avatars/", blank=True)
    is_enabled = models.BooleanField(default=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="chat_avatars",
    )
    group = models.ForeignKey(
        "groups.SecurityGroup",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="chat_avatars",
    )

    class Meta:
        ordering = ["name", "pk"]
        verbose_name = _("Chat Avatar")
        verbose_name_plural = _("Chat Avatars")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def clean(self):
        super().clean()
        owners = [field for field in ("user", "group") if getattr(self, f"{field}_id")]
        if len(owners) > 1:
            raise ValidationError(
                {
                    field: _("Select either a user or a security group, not both.")
                    for field in owners
                }
            )
        if not owners:
            raise ValidationError(
                _("Avatars must be assigned to a user or a security group."),
            )

    @property
    def owner(self):
        return self.user if self.user_id else self.group

    def owner_display(self) -> str:
        owner = self.owner
        if owner is None:
            return ""
        if hasattr(owner, "get_username"):
            return owner.get_username()
        if hasattr(owner, "name"):
            return owner.name
        return str(owner)

    def is_available(self) -> bool:
        if not self.is_enabled:
            return False
        if self.user_id:
            return bool(getattr(self.user, "is_online", False))
        if self.group_id:
            for member in self.group.user_set.all():
                if getattr(member, "is_online", False):
                    return True
        return False

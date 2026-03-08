from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import Ownable


class ChatAvatar(Ownable):
    """Represents an operator identity for handling chats."""

    name = models.CharField(max_length=150)
    photo = models.ImageField(upload_to="chats/avatars/", blank=True)
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "pk"]
        verbose_name = _("Chat Avatar")
        verbose_name_plural = _("Chat Avatars")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

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

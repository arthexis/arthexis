from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .models import Entity


class Ownable(Entity):
    """Abstract base class for models owned by a user or security group."""

    owner_required: bool = True

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
        help_text=_("User that owns this object."),
    )
    group = models.ForeignKey(
        "groups.SecurityGroup",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
        help_text=_("Security group that owns this object."),
    )

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(
                condition=(
                    (Q(user__isnull=True) & Q(group__isnull=True))
                    | (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="%(app_label)s_%(class)s_owner_exclusive",
            )
        ]

    def clean(self):
        """Validate mutually exclusive ownership requirements."""
        super().clean()
        provided = [
            field for field in ("user", "group") if getattr(self, f"{field}_id")
        ]
        if len(provided) > 1:
            raise ValidationError(
                {
                    field: _("Select either a user or a security group, not both.")
                    for field in provided
                }
            )
        if self.owner_required and not provided:
            raise ValidationError(
                _("Ownable objects must be assigned to a user or a security group."),
            )

    @property
    def owner(self):
        """Return the active owner (user or security group)."""
        return self.user if self.user_id else self.group

    def owner_display(self) -> str:
        """Return a human-readable label for the owner."""
        owner = self.owner
        if owner is None:
            return ""
        if hasattr(owner, "get_username"):
            return owner.get_username()
        if hasattr(owner, "name"):
            return owner.name
        return str(owner)

    def owner_members(self) -> list[str]:
        """Return usernames for members who own the record."""
        if self.user_id and self.user:
            return [self.user.get_username()]
        if self.group_id and self.group:
            return [member.get_username() for member in self.group.user_set.all()]
        return []

    def resolve_profile_field_value(self, key: str):
        """Resolve owner-related fields for profile rendering."""
        normalized = key.lower()
        if normalized == "owner":
            return True, self.owner
        if normalized == "owners":
            return True, self.owner_members()
        return False, None


__all__ = ["Ownable"]

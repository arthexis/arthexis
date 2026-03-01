"""Chat preference profile models."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from .profile import Profile


class ChatProfile(Profile):
    """Persist chat contact preference for a user, group, or avatar owner."""

    is_enabled = models.BooleanField(
        default=True,
        db_default=True,
        verbose_name=_("Enabled"),
        help_text=_("Disable this profile without deleting the saved preference."),
    )
    contact_via_chat = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name=_("I would like to be contacted via chat"),
        help_text=_("Allow support staff to contact this owner using the chat channel."),
    )

    class Meta(Profile.Meta):
        verbose_name = _("Chat Profile")
        verbose_name_plural = _("Chat Profiles")
        constraints = [
            models.CheckConstraint(
                name="chat_profile_exactly_one_owner",
                condition=(
                    models.Q(user__isnull=False, group__isnull=True, avatar__isnull=True)
                    | models.Q(user__isnull=True, group__isnull=False, avatar__isnull=True)
                    | models.Q(user__isnull=True, group__isnull=True, avatar__isnull=False)
                ),
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(user__isnull=False),
                name="chat_profile_unique_user",
            ),
            models.UniqueConstraint(
                fields=["group"],
                condition=models.Q(group__isnull=False),
                name="chat_profile_unique_group",
            ),
            models.UniqueConstraint(
                fields=["avatar"],
                condition=models.Q(avatar__isnull=False),
                name="chat_profile_unique_avatar",
            ),
        ]

    def __str__(self) -> str:
        """Return a concise label for admin listings."""

        return _("Chat profile for %(owner)s") % {"owner": self.owner_display()}

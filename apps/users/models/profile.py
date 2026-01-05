from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import Ownable


class Profile(Ownable):
    """Abstract base class for user or group scoped configuration."""

    owner_required = False

    avatar = models.ForeignKey(
        "chats.ChatAvatar",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
        help_text=_("Avatar that owns this profile when not linked directly to a user or group."),
    )

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        owner_fields = {
            "user": self.user_id,
            "group": self.group_id,
            "avatar": self.avatar_id,
        }
        provided = [field for field, value in owner_fields.items() if value]
        if len(provided) > 1:
            raise ValidationError(
                {
                    field: _("Select only one owner between user, group, and avatar.")
                    for field in provided
                }
            )
        if not provided:
            raise ValidationError(
                _("Profiles must be assigned to a user, security group, or avatar."),
            )
        if self.avatar_id:
            return
        if self.user_id:
            user_model = get_user_model()
            username_cache = {"value": None}

            def _resolve_username():
                if username_cache["value"] is not None:
                    return username_cache["value"]
                user_obj = getattr(self, "user", None)
                username = getattr(user_obj, "username", None)
                if not username:
                    manager = getattr(
                        user_model, "all_objects", user_model._default_manager
                    )
                    username = (
                        manager.filter(pk=self.user_id)
                        .values_list("username", flat=True)
                        .first()
                    )
                username_cache["value"] = username
                return username

            is_restricted = getattr(user_model, "is_profile_restricted_username", None)
            if callable(is_restricted):
                username = _resolve_username()
                if is_restricted(username):
                    raise ValidationError(
                        {
                            "user": _(
                                "The %(username)s account cannot have profiles attached."
                            )
                            % {"username": username}
                        }
                    )
            else:
                system_username = getattr(user_model, "SYSTEM_USERNAME", None)
                if system_username:
                    username = _resolve_username()
                    if user_model.is_system_username(username):
                        raise ValidationError(
                            {
                                "user": _(
                                    "The %(username)s account cannot have profiles attached."
                                )
                                % {"username": username}
                            }
                        )

    @property
    def owner(self):
        """Return the assigned user or group."""

        if self.avatar_id:
            return self.avatar
        return self.user if self.user_id else self.group

    def owner_display(self) -> str:
        """Return a human readable owner label."""

        owner = self.owner
        if owner is None:  # pragma: no cover - guarded by ``clean``
            return ""
        if hasattr(owner, "get_username"):
            return owner.get_username()
        if hasattr(owner, "name"):
            return owner.name
        return str(owner)

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.core.models.ownable import Ownable


class Profile(Ownable):
    """Abstract base class for user or security group scoped configuration."""

    owner_required = False

    class Meta:
        abstract = True

    def clean(self):
        owner_fields = {"user": self.user_id, "group": self.group_id}
        provided = [field for field, value in owner_fields.items() if value]
        if len(provided) > 1:
            raise ValidationError(
                {
                    field: _("Select either a user or a security group, not both.")
                    for field in provided
                }
            )
        if self.owner_required and not provided:
            raise ValidationError(
                _("Profiles must be assigned to a user or security group."),
            )
        super().clean()
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

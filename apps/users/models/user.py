from typing import Type

from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.core.exceptions import ValidationError
from django.core.validators import validate_ipv46_address
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_variables

from apps.base.models import Entity, EntityUserManager
from apps.users import temp_passwords

from .profile import Profile


class User(Entity, AbstractUser):
    SYSTEM_USERNAME = "arthexis"
    ADMIN_USERNAME = "admin"
    PROFILE_RESTRICTED_USERNAMES = frozenset()

    objects = EntityUserManager()
    all_objects = DjangoUserManager()
    """Custom user model."""
    data_path = models.CharField(max_length=255, blank=True)
    last_visit_ip_address = models.CharField(
        max_length=45,
        blank=True,
        validators=[validate_ipv46_address],
    )
    operate_as = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="operated_users",
        help_text=(
            "Operate using another user's permissions when additional authority is "
            "required."
        ),
    )
    site_template = models.ForeignKey(
        "pages.SiteTemplate",
        on_delete=models.SET_NULL,
        related_name="users",
        null=True,
        blank=True,
        verbose_name=_("Site template"),
        help_text=_(
            "Branding template to apply for this user when overriding the site default."
        ),
    )
    is_active = models.BooleanField(
        _("active"),
        default=True,
        help_text=(
            "Designates whether this user should be treated as active. Unselect this instead of deleting customer accounts."
        ),
    )
    require_2fa = models.BooleanField(
        _("require 2FA"),
        default=False,
        help_text=_("Require both a password and authenticator code to sign in."),
    )
    temporary_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Automatically deactivate this account after the selected date and time."),
    )

    def __str__(self):
        return self.username

    @classmethod
    def is_system_username(cls, username):
        return bool(username) and username == cls.SYSTEM_USERNAME

    @sensitive_variables("raw_password")
    def set_password(self, raw_password):
        result = super().set_password(raw_password)
        temp_passwords.discard_temp_password(self.username)
        return result

    @sensitive_variables("raw_password")
    def check_password(self, raw_password):
        if self._deactivate_if_expired():
            return False
        if super().check_password(raw_password):
            return True
        if raw_password is None:
            return False
        entry = temp_passwords.load_temp_password(self.username)
        if entry is None:
            return False
        if entry.is_expired:
            temp_passwords.discard_temp_password(self.username)
            return False
        if not entry.allow_change:
            return False
        return entry.check_password(raw_password)

    def _normalized_expiration(self):
        expires_at = self.temporary_expires_at
        if expires_at and timezone.is_naive(expires_at):
            expires_at = timezone.make_aware(expires_at)
        return expires_at

    @property
    def is_temporary(self) -> bool:
        return self.temporary_expires_at is not None

    @property
    def is_temporarily_expired(self) -> bool:
        expires_at = self._normalized_expiration()
        return bool(expires_at and timezone.now() >= expires_at)

    def _deactivate_if_expired(self, *, save: bool = True) -> bool:
        if not self.is_temporarily_expired:
            return False
        updates = []
        normalized_expiration = self._normalized_expiration()
        if normalized_expiration and self.temporary_expires_at != normalized_expiration:
            self.temporary_expires_at = normalized_expiration
            updates.append("temporary_expires_at")
        if self.is_active:
            self.is_active = False
            updates.append("is_active")
        temp_passwords.discard_temp_password(self.username)
        if updates and save and self.pk:
            type(self).all_objects.filter(pk=self.pk).update(
                **{field: getattr(self, field) for field in updates}
            )
        return True

    def deactivate_temporary_credentials(self):
        if self.temporary_expires_at is None or self.temporary_expires_at > timezone.now():
            self.temporary_expires_at = timezone.now()
        self.is_active = False
        temp_passwords.discard_temp_password(self.username)
        updates = ["temporary_expires_at", "is_active"]
        if self.pk:
            type(self).all_objects.filter(pk=self.pk).update(
                temporary_expires_at=self.temporary_expires_at, is_active=self.is_active
            )

    @classmethod
    def is_profile_restricted_username(cls, username):
        return bool(username) and username in cls.PROFILE_RESTRICTED_USERNAMES

    @property
    def is_system_user(self) -> bool:
        return self.is_system_username(self.username)

    @property
    def is_profile_restricted(self) -> bool:
        return self.is_profile_restricted_username(self.username)

    def clean(self):
        super().clean()
        if not self.operate_as_id:
            return
        try:
            delegate = self.operate_as
        except type(self).DoesNotExist:
            raise ValidationError({"operate_as": _("Selected user is not available.")})
        errors = []
        if delegate.pk == self.pk:
            errors.append(_("Cannot operate as yourself."))
        if getattr(delegate, "is_deleted", False):
            errors.append(_("Cannot operate as a deleted user."))
        if not self.is_staff:
            errors.append(_("Only staff members may operate as another user."))
        if delegate.is_staff and not self.is_superuser:
            errors.append(_("Only superusers may operate as staff members."))
        if errors:
            raise ValidationError({"operate_as": errors})

    def _delegate_for_permissions(self):
        if not self.is_staff or not self.operate_as_id:
            return None
        try:
            delegate = self.operate_as
        except type(self).DoesNotExist:
            return None
        if delegate.pk == self.pk:
            return None
        if getattr(delegate, "is_deleted", False):
            return None
        if delegate.is_staff and not self.is_superuser:
            return None
        return delegate

    def _check_operate_as_chain(self, predicate, visited=None):
        if visited is None:
            visited = set()
        identifier = self.pk or id(self)
        if identifier in visited:
            return False
        visited.add(identifier)
        if predicate(self):
            return True
        delegate = self._delegate_for_permissions()
        if not delegate:
            return False
        return delegate._check_operate_as_chain(predicate, visited)

    def has_perm(self, perm, obj=None):
        return self._check_operate_as_chain(
            lambda user: super(User, user).has_perm(perm, obj)
        )

    def has_module_perms(self, app_label):
        return self._check_operate_as_chain(
            lambda user: super(User, user).has_module_perms(app_label)
        )

    def _profile_for(self, profile_cls: Type[Profile], user: "User"):
        queryset = profile_cls.objects.all()
        if hasattr(profile_cls, "is_enabled"):
            queryset = queryset.filter(is_enabled=True)

        group_ids = list(user.groups.values_list("id", flat=True))
        owner_filter = Q(user=user)
        if group_ids:
            owner_filter |= Q(group_id__in=group_ids)
        if hasattr(profile_cls, "avatar"):
            owner_filter |= Q(avatar__user=user)
            if group_ids:
                owner_filter |= Q(avatar__group_id__in=group_ids)

        return queryset.filter(owner_filter).first()

    def get_profile(self, profile_cls: Type[Profile]):
        """Return the first matching profile for the user or their delegate chain."""

        if not isinstance(profile_cls, type) or not issubclass(profile_cls, Profile):
            raise TypeError("profile_cls must be a Profile subclass")

        result = None

        def predicate(user: "User"):
            nonlocal result
            result = self._profile_for(profile_cls, user)
            return result is not None

        self._check_operate_as_chain(predicate)
        return result

    def has_profile(self, profile_cls: Type[Profile]) -> bool:
        """Return ``True`` when a profile is available for the user or delegate chain."""

        return self.get_profile(profile_cls) is not None

    def _direct_profile(self, model_label: str, app_label: str = "core"):
        model = apps.get_model(app_label, model_label)
        try:
            return self.get_profile(model)
        except TypeError:
            return None

    def get_phones_by_priority(self):
        """Return a list of ``UserPhoneNumber`` instances ordered by priority."""

        ordered_numbers = self.phone_numbers.order_by("priority", "pk")
        return list(ordered_numbers)

    def get_phone_numbers_by_priority(self):
        """Backward-compatible alias for :meth:`get_phones_by_priority`."""

        return self.get_phones_by_priority()

    @property
    def release_manager(self):
        return self._direct_profile("ReleaseManager")

    @property
    def odoo_employee(self):
        return self._direct_profile("OdooEmployee", app_label="odoo")

    @property
    def odoo_profile(self):
        return self.odoo_employee

    @property
    def social_profile(self):
        avatars = getattr(self, "chat_avatars", None)
        if avatars is None:
            return None

        for avatar in avatars.all():
            for app_label, model_name in (
                ("socials", "BlueskyProfile"),
                ("socials", "DiscordProfile"),
            ):
                model = apps.get_model(app_label, model_name)
                profile = model.objects.filter(avatar=avatar).first()
                if profile is not None:
                    return profile
        return None

    class Meta(AbstractUser.Meta):
        verbose_name = _("User")
        verbose_name_plural = _("Users")
        db_table = "core_user"

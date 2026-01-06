from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity

__all__ = [
    "Ownable",
    "OwnedObjectLink",
    "get_owned_objects_for_group",
    "get_owned_objects_for_user",
    "get_ownable_models",
]


@dataclass
class OwnedObjectLink:
    label: str
    url: str | None
    model_label: str
    via: str | None = None


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
        super().clean()
        provided = [
            field for field in ("user", "group") if getattr(self, f"{field}_id")
        ]
        if len(provided) > 1:
            raise ValidationError(
                {field: _("Select either a user or a security group, not both.") for field in provided}
            )
        if self.owner_required and not provided:
            raise ValidationError(
                _("Ownable objects must be assigned to a user or a security group."),
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

    def owner_members(self) -> list[str]:
        if self.user_id and self.user:
            return [self.user.get_username()]
        if self.group_id and self.group:
            return [member.get_username() for member in self.group.user_set.all()]
        return []

    def resolve_profile_field_value(self, key: str):
        normalized = key.lower()
        if normalized == "owner":
            return True, self.owner
        if normalized == "owners":
            return True, self.owner_members()
        return False, None


def get_ownable_models() -> Sequence[type[Ownable]]:
    return tuple(
        model
        for model in apps.get_models()
        if isinstance(model, type)
        and issubclass(model, Ownable)
        and not model._meta.abstract
    )


def _ownable_admin_url(obj: Ownable) -> str | None:
    opts = obj._meta
    try:
        return reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[obj.pk])
    except NoReverseMatch:
        return None


def _build_links(objects: Iterable[Ownable], via: str | None = None) -> list[OwnedObjectLink]:
    links: list[OwnedObjectLink] = []
    for obj in objects:
        opts = obj._meta
        links.append(
            OwnedObjectLink(
                label=str(obj),
                url=_ownable_admin_url(obj),
                model_label=str(opts.verbose_name).title(),
                via=via,
            )
        )
    return links


def get_owned_objects_for_user(user) -> tuple[list[OwnedObjectLink], list[OwnedObjectLink]]:
    direct: list[OwnedObjectLink] = []
    via_groups: list[OwnedObjectLink] = []
    groups = list(getattr(user, "groups", []).all()) if hasattr(user, "groups") else []

    for model in get_ownable_models():
        manager = getattr(model, "_default_manager", None)
        if manager is None:
            continue
        if user is not None:
            direct.extend(_build_links(manager.filter(user=user)))
        if groups:
            for group in groups:
                group_links = _build_links(
                    manager.filter(group=group).exclude(user=user), via=str(group)
                )
                via_groups.extend(group_links)
    return direct, via_groups


def get_owned_objects_for_group(group) -> tuple[list[OwnedObjectLink], list[OwnedObjectLink]]:
    direct: list[OwnedObjectLink] = []
    member_owned: list[OwnedObjectLink] = []
    members = list(group.user_set.all()) if group is not None else []

    for model in get_ownable_models():
        manager = getattr(model, "_default_manager", None)
        if manager is None:
            continue
        direct.extend(_build_links(manager.filter(group=group)))
        if members:
            for member in members:
                member_owned.extend(
                    _build_links(manager.filter(user=member), via=member.get_username())
                )
    return direct, member_owned

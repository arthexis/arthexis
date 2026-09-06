from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from django.apps import apps
from django.db.utils import OperationalError, ProgrammingError
from django.urls import NoReverseMatch, reverse

from apps.base.ownership import Ownable

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


def get_ownable_models() -> Sequence[type[Ownable]]:
    """Return all concrete Ownable models registered in the project."""
    return tuple(
        model
        for model in apps.get_models()
        if isinstance(model, type)
        and issubclass(model, Ownable)
        and not model._meta.abstract
    )


def _ownable_admin_url(obj: Ownable) -> str | None:
    """Return the admin change URL for a owned object when available."""
    opts = obj._meta
    try:
        return reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[obj.pk])
    except NoReverseMatch:
        return None


def _build_links(objects: Iterable[Ownable], via: str | None = None) -> list[OwnedObjectLink]:
    """Build admin links for owned objects."""
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


def _build_links_if_table_exists(
    objects: Iterable[Ownable], via: str | None = None
) -> list[OwnedObjectLink]:
    """Build links, skipping optional app models whose migration table is absent."""

    try:
        return _build_links(objects, via=via)
    except (OperationalError, ProgrammingError):
        return []


def get_owned_objects_for_user(user) -> tuple[list[OwnedObjectLink], list[OwnedObjectLink]]:
    """Return owned object links grouped by direct and group ownership."""
    direct: list[OwnedObjectLink] = []
    via_groups: list[OwnedObjectLink] = []
    groups = list(getattr(user, "groups", []).all()) if hasattr(user, "groups") else []

    for model in get_ownable_models():
        manager = getattr(model, "_default_manager", None)
        if manager is None:
            continue
        if user is not None:
            direct.extend(_build_links_if_table_exists(manager.filter(user=user)))
        if groups:
            for group in groups:
                group_links = _build_links_if_table_exists(
                    manager.filter(group=group).exclude(user=user), via=str(group)
                )
                via_groups.extend(group_links)
    return direct, via_groups


def get_owned_objects_for_group(group) -> tuple[list[OwnedObjectLink], list[OwnedObjectLink]]:
    """Return owned objects for a group and its members."""
    direct: list[OwnedObjectLink] = []
    member_owned: list[OwnedObjectLink] = []
    members = list(group.user_set.all()) if group is not None else []

    for model in get_ownable_models():
        manager = getattr(model, "_default_manager", None)
        if manager is None:
            continue
        direct.extend(_build_links_if_table_exists(manager.filter(group=group)))
        if members:
            for member in members:
                member_owned.extend(
                    _build_links_if_table_exists(
                        manager.filter(user=member), via=member.get_username()
                    )
                )
    return direct, member_owned

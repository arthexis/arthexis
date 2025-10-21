"""Utility helpers for working with Reference objects."""

from __future__ import annotations

from typing import Iterable, TYPE_CHECKING

from django.contrib.sites.models import Site

if TYPE_CHECKING:  # pragma: no cover - imported only for type checking
    from django.http import HttpRequest
    from nodes.models import Node
    from .models import Reference


def filter_visible_references(
    refs: Iterable["Reference"],
    *,
    request: "HttpRequest | None" = None,
    site: Site | None = None,
    node: "Node | None" = None,
    respect_footer_visibility: bool = True,
) -> list["Reference"]:
    """Return references visible for the current context."""

    if site is None and request is not None:
        try:
            host = request.get_host().split(":")[0]
        except Exception:
            host = ""
        if host:
            site = Site.objects.filter(domain__iexact=host).first()

    site_id = getattr(site, "pk", None)

    if node is None:
        try:
            from nodes.models import Node  # imported lazily to avoid circular import

            node = Node.get_local()
        except Exception:
            node = None

    node_role_id = getattr(node, "role_id", None)
    node_active_feature_ids: set[int] = set()
    if node is not None:
        assignments_manager = getattr(node, "feature_assignments", None)
        if assignments_manager is not None:
            try:
                assignments = list(
                    assignments_manager.filter(is_deleted=False).select_related(
                        "feature"
                    )
                )
            except Exception:
                assignments = []
            for assignment in assignments:
                feature = getattr(assignment, "feature", None)
                if feature is None or getattr(feature, "is_deleted", False):
                    continue
                try:
                    if feature.is_enabled:
                        node_active_feature_ids.add(feature.pk)
                except Exception:
                    continue

    visible_refs: list["Reference"] = []
    for ref in refs:
        required_roles = {role.pk for role in ref.roles.all()}
        required_features = {feature.pk for feature in ref.features.all()}
        required_sites = {current_site.pk for current_site in ref.sites.all()}

        if required_roles or required_features or required_sites:
            allowed = True
            if required_roles:
                allowed = bool(node_role_id and node_role_id in required_roles)
            if allowed and required_features:
                allowed = bool(
                    node_active_feature_ids
                    and node_active_feature_ids.intersection(required_features)
                )
            if allowed and required_sites:
                allowed = bool(site_id and site_id in required_sites)

            if not allowed:
                continue

        if respect_footer_visibility:
            if ref.footer_visibility == ref.FOOTER_PUBLIC:
                visible_refs.append(ref)
            elif (
                ref.footer_visibility == ref.FOOTER_PRIVATE
                and request
                and request.user.is_authenticated
            ):
                visible_refs.append(ref)
            elif (
                ref.footer_visibility == ref.FOOTER_STAFF
                and request
                and request.user.is_authenticated
                and request.user.is_staff
            ):
                visible_refs.append(ref)
        else:
            visible_refs.append(ref)

    return visible_refs


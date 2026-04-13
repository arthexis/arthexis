"""Utility helpers for link references and reference attachments."""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING, Iterable
from urllib.parse import urlparse

from django.contrib.contenttypes.models import ContentType
from django.contrib.sites.models import Site

if TYPE_CHECKING:  # pragma: no cover - imported only for type checking
    from django.db import models
    from django.http import HttpRequest

    from apps.nodes.models import Node

    from .models import Reference, ReferenceAttachment


DEFAULT_REFERENCE_SLOT = "default"
REFERENCE_SLOT_BY_MODEL_LABEL = {
    "cards.rfid": "rfid",
    "ocpp.charger": "charger",
    "terms.term": "term",
}


def default_reference_slot(
    instance: "models.Model", *, fallback: str = DEFAULT_REFERENCE_SLOT
) -> str:
    """Return the default attachment slot name for ``instance``."""

    model_label = instance._meta.label_lower
    return REFERENCE_SLOT_BY_MODEL_LABEL.get(model_label, fallback)


def mirror_legacy_reference_attachment(
    instance: "models.Model",
    *,
    legacy_field: str = "reference",
    slot: str | None = None,
    update_fields: set[str] | None = None,
) -> "ReferenceAttachment | None":
    """Mirror ``instance.<legacy_field>`` into ``ReferenceAttachment``."""

    if not getattr(instance, "pk", None):
        return None
    if update_fields is not None and legacy_field not in update_fields:
        return None

    from .models import ReferenceAttachment

    attachment_slot = slot or default_reference_slot(instance)
    content_type = ContentType.objects.get_for_model(
        instance, for_concrete_model=True
    )
    reference = getattr(instance, legacy_field, None)
    if reference is None:
        ReferenceAttachment.objects.filter(
            content_type=content_type,
            object_id=str(instance.pk),
            slot=attachment_slot,
            is_primary=True,
        ).delete()
        return None

    attachment, _ = ReferenceAttachment.objects.update_or_create(
        content_type=content_type,
        object_id=str(instance.pk),
        slot=attachment_slot,
        is_primary=True,
        defaults={"reference": reference},
    )
    return attachment


def get_attached_references(
    instance: "models.Model",
    *,
    slot: str | None = None,
    legacy_field: str = "reference",
) -> list["Reference"]:
    """Return references for ``instance``, preferring attachments first."""

    if not getattr(instance, "pk", None):
        return []

    from .models import ReferenceAttachment

    content_type = ContentType.objects.get_for_model(
        instance, for_concrete_model=True
    )
    queryset = ReferenceAttachment.objects.filter(
        content_type=content_type,
        object_id=str(instance.pk),
    ).select_related("reference")
    if slot is not None:
        queryset = queryset.filter(slot=slot)
    references = [attachment.reference for attachment in queryset]
    if references:
        return references

    legacy_reference = getattr(instance, legacy_field, None)
    if legacy_reference is None:
        return []
    return [legacy_reference]


def get_primary_reference(
    instance: "models.Model",
    *,
    slot: str | None = None,
    legacy_field: str = "reference",
) -> "Reference | None":
    """Return one reference for integrations/UI using staged fallback logic."""

    if not getattr(instance, "pk", None):
        return None

    from .models import ReferenceAttachment

    content_type = ContentType.objects.get_for_model(
        instance, for_concrete_model=True
    )
    queryset = ReferenceAttachment.objects.filter(
        content_type=content_type,
        object_id=str(instance.pk),
        is_primary=True,
    ).select_related("reference")
    if slot is not None:
        queryset = queryset.filter(slot=slot)

    attachment = queryset.order_by("sort_order", "id").first()
    if attachment is not None:
        return attachment.reference

    legacy_reference = getattr(instance, legacy_field, None)
    return legacy_reference


def _normalize_host(host: str | None) -> str:
    """Return a trimmed host string without surrounding brackets."""

    if not host:
        return ""
    host = host.strip()
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def host_is_local_loopback(host: str | None) -> bool:
    """Return ``True`` when the host string points to 127.0.0.1."""

    normalized = _normalize_host(host)
    if not normalized:
        return False
    try:
        return ipaddress.ip_address(normalized) == ipaddress.ip_address("127.0.0.1")
    except ValueError:
        return False


def url_targets_local_loopback(url: str | None) -> bool:
    """Return ``True`` when the parsed URL host equals 127.0.0.1."""

    if not url:
        return False
    parsed = urlparse(url)
    return host_is_local_loopback(parsed.hostname)


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
            from apps.nodes.models import (
                Node,  # imported lazily to avoid circular import
            )

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
                    assignments_manager.filter(is_deleted=False).select_related("feature")
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
        if not ref.is_link_valid():
            continue

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


__all__ = [
    "DEFAULT_REFERENCE_SLOT",
    "default_reference_slot",
    "filter_visible_references",
    "get_attached_references",
    "get_primary_reference",
    "host_is_local_loopback",
    "mirror_legacy_reference_attachment",
    "url_targets_local_loopback",
]

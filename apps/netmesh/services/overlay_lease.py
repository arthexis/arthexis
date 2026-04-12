"""Overlay IPv4 lease allocation helpers for mesh memberships."""

from __future__ import annotations

import ipaddress

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.netmesh.models import MeshMembership, MeshOverlayLease


def _overlay_pool() -> ipaddress.IPv4Network:
    cidr = getattr(settings, "NETMESH_OVERLAY_IPV4_CIDR", "100.96.0.0/16")
    return ipaddress.IPv4Network(cidr, strict=False)


def _scope_filters(*, membership: MeshMembership) -> dict:
    filters = {"tenant": membership.tenant}
    if membership.site_id:
        filters["site_id"] = membership.site_id
    else:
        filters["site__isnull"] = True
    return filters


def _first_free_address(*, membership: MeshMembership) -> str:
    pool = _overlay_pool()
    used_ips = {
        int(ipaddress.IPv4Address(value))
        for value in MeshOverlayLease.objects.filter(**_scope_filters(membership=membership)).values_list(
            "overlay_ipv4", flat=True
        )
    }
    for candidate in pool.hosts():
        candidate_int = int(candidate)
        if candidate_int in used_ips:
            continue
        return str(candidate)
    raise RuntimeError(f"No free overlay IPv4 addresses remain in pool {pool}.")


def ensure_overlay_lease(*, membership: MeshMembership, retries: int = 3) -> MeshOverlayLease:
    if not membership.is_enabled:
        raise ValueError("Cannot assign overlay lease to disabled membership.")

    scope_values = {
        "tenant": membership.tenant,
        "site_id": membership.site_id,
    }

    for _ in range(retries):
        with transaction.atomic():
            existing = MeshOverlayLease.objects.select_for_update().filter(membership=membership).first()
            if existing:
                scope_changed = any(getattr(existing, field_name) != value for field_name, value in scope_values.items())
                if scope_changed:
                    for field_name, value in scope_values.items():
                        setattr(existing, field_name, value)
                    existing.overlay_ipv4 = _first_free_address(membership=membership)
                    existing.full_clean()
                    existing.save(update_fields=[*scope_values.keys(), "overlay_ipv4"])
                return existing

            MeshMembership.objects.select_for_update().filter(pk=membership.pk).exists()
            overlay_ipv4 = _first_free_address(membership=membership)
            try:
                lease = MeshOverlayLease.objects.create(
                    membership=membership,
                    overlay_ipv4=overlay_ipv4,
                    **scope_values,
                )
            except IntegrityError:
                continue
            lease.full_clean()
            return lease

    raise RuntimeError("Failed to allocate overlay IPv4 lease after retries due to collisions.")


def release_overlay_lease(*, membership: MeshMembership) -> None:
    MeshOverlayLease.objects.filter(membership=membership).delete()

"""Serialization helpers for Netmesh API payloads."""

from __future__ import annotations

from apps.netmesh.models import NodeKeyMaterial


def serialize_active_transport_key(*, key_material: NodeKeyMaterial | None) -> dict[str, object] | None:
    if key_material is None:
        return None
    return {
        "type": key_material.key_type,
        "version": key_material.key_version,
        "state": key_material.key_state,
        "public_key": key_material.public_key,
        "created_at": key_material.created_at.isoformat(),
        "rotated_at": key_material.rotated_at.isoformat() if key_material.rotated_at else None,
    }

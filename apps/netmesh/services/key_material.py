"""Netmesh transport key generation and rotation helpers."""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from django.db import transaction
from django.utils import timezone

from apps.netmesh.models import NodeKeyMaterial
from apps.nodes.models import Node


def _serialize_x25519_public_key(public_key: x25519.X25519PublicKey) -> str:
    encoded = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return "x25519:" + base64.b64encode(encoded).decode("ascii")


def _validate_transport_key_not_bootstrap_identity(*, node: Node, transport_public_key: str) -> None:
    bootstrap_public_key = (node.public_key or "").strip()
    if bootstrap_public_key and bootstrap_public_key == transport_public_key:
        raise ValueError("transport packet key must not reuse enrollment identity key material")


def generate_transport_keypair(*, node: Node, key_type: str = NodeKeyMaterial.KeyType.X25519) -> tuple[str, str]:
    if key_type != NodeKeyMaterial.KeyType.X25519:
        raise ValueError(f"Unsupported transport key type: {key_type}")
    private_key = x25519.X25519PrivateKey.generate()
    private_payload = base64.b64encode(
        private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode("ascii")
    public_payload = _serialize_x25519_public_key(private_key.public_key())
    _validate_transport_key_not_bootstrap_identity(node=node, transport_public_key=public_payload)
    return private_payload, public_payload


@transaction.atomic
def ensure_active_transport_key(*, node: Node, key_type: str = NodeKeyMaterial.KeyType.X25519) -> tuple[NodeKeyMaterial, str]:
    active = (
        NodeKeyMaterial.objects.select_for_update()
        .filter(node=node, key_state=NodeKeyMaterial.KeyState.ACTIVE)
        .order_by("-key_version", "-created_at", "-id")
        .first()
    )
    if active:
        return active, ""
    private_key, public_key = generate_transport_keypair(node=node, key_type=key_type)
    key_material = NodeKeyMaterial.objects.create(
        node=node,
        key_type=key_type,
        key_version=1,
        public_key=public_key,
        key_state=NodeKeyMaterial.KeyState.ACTIVE,
    )
    return key_material, private_key


@transaction.atomic
def rotate_transport_key(*, node: Node, key_type: str = NodeKeyMaterial.KeyType.X25519) -> tuple[NodeKeyMaterial, str]:
    now = timezone.now()
    active = (
        NodeKeyMaterial.objects.select_for_update()
        .filter(node=node, key_state=NodeKeyMaterial.KeyState.ACTIVE)
        .order_by("-key_version", "-created_at", "-id")
        .first()
    )
    next_version = 1
    if active:
        active.key_state = NodeKeyMaterial.KeyState.RETIRED
        active.rotated_at = now
        active.revoked_at = now
        active.save(update_fields=["key_state", "rotated_at", "revoked_at", "revoked"])
        next_version = active.key_version + 1
    private_key, public_key = generate_transport_keypair(node=node, key_type=key_type)
    key_material = NodeKeyMaterial.objects.create(
        node=node,
        key_type=key_type,
        key_version=next_version,
        public_key=public_key,
        key_state=NodeKeyMaterial.KeyState.ACTIVE,
    )
    return key_material, private_key

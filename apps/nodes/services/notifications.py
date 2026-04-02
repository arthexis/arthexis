"""Peer notification services for nodes."""

from __future__ import annotations

import logging
from secrets import token_hex

from cryptography.hazmat.primitives import serialization

from apps.nodes.services.transport import send_registration
from apps.nodes.models.utils import _format_upgrade_body

logger = logging.getLogger(__name__)


def notify_peers_of_update(node) -> None:
    """Attempt to update ``node`` registration with known peers."""
    security_dir = node.get_base_path() / "security"
    priv_path = security_dir / f"{node.public_endpoint}"
    if not priv_path.exists():
        logger.debug("Private key for %s not found; skipping peer update", node)
        return
    try:
        private_key = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
    except Exception as exc:
        logger.warning("Failed to load private key for %s: %s", node, exc)
        return

    token = token_hex(16)
    signature, error = node.__class__.sign_payload(token, private_key)
    if not signature:
        logger.warning("Failed to sign peer update for %s: %s", node, error)
        return

    payload = {
        "hostname": node.hostname,
        "network_hostname": node.network_hostname,
        "address": node.address,
        "ipv4_address": node.ipv4_address,
        "ipv6_address": node.ipv6_address,
        "port": node.port,
        "mac_address": node.mac_address,
        "public_key": node.public_key,
        "token": token,
        "signature": signature,
    }
    if node.installed_version:
        payload["installed_version"] = node.installed_version
    if node.installed_revision:
        payload["installed_revision"] = node.installed_revision

    peers = node.__class__.objects.exclude(pk=node.pk)
    for peer in peers:
        if not send_registration(payload, peer):
            logger.warning("Unable to notify node %s of startup", peer)
            continue
        version_display = _format_upgrade_body(node.installed_version, node.installed_revision)
        version_suffix = f" ({version_display})" if version_display else ""
        logger.info("Announced startup to %s%s", peer, version_suffix)

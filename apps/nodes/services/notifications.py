"""Peer notification services for nodes."""

from __future__ import annotations

import json
import logging
from secrets import token_hex
from urllib.parse import urlparse, urlunsplit

from cryptography.hazmat.primitives import serialization

from apps.nodes.models.utils import _format_upgrade_body

logger = logging.getLogger(__name__)


def notify_peers_of_update(node) -> None:
    """Attempt to update ``node`` registration with known peers."""
    try:
        import requests
    except Exception:  # pragma: no cover
        return

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

    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    headers = {"Content-Type": "application/json"}

    peers = node.__class__.objects.exclude(pk=node.pk)
    for peer in peers:
        host_candidates = peer.get_remote_host_candidates()
        port = peer.port or 8888
        urls: list[str] = []
        scheme_candidates = peer.iter_preferred_schemes()
        for host in host_candidates:
            host = host.strip()
            if not host:
                continue
            if host.startswith("http://") or host.startswith("https://"):
                parsed = urlparse(host)
                netloc = parsed.netloc or parsed.path
                base_path = (parsed.path or "").rstrip("/")
                for scheme in peer.iter_preferred_schemes(default=parsed.scheme or "http"):
                    candidate = urlunsplit((scheme, netloc, base_path, "", "")).rstrip("/")
                    if candidate and candidate not in urls:
                        urls.append(candidate)
                continue
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            for scheme in scheme_candidates:
                scheme_default_port = 443 if scheme == "https" else 80
                if port in {80, 443} and port != scheme_default_port:
                    scheme_port = None
                else:
                    scheme_port = port
                if scheme_port and scheme_port != scheme_default_port:
                    candidate = f"{scheme}://{host}:{scheme_port}/nodes/register/"
                else:
                    candidate = f"{scheme}://{host}/nodes/register/"
                if candidate not in urls:
                    urls.append(candidate)
        if not urls:
            continue
        for url in urls:
            try:
                response = requests.post(url, data=payload_json, headers=headers, timeout=2)
            except Exception as exc:
                logger.debug("Failed to update %s via %s: %s", peer, url, exc)
                continue
            if response.ok:
                version_display = _format_upgrade_body(node.installed_version, node.installed_revision)
                version_suffix = f" ({version_display})" if version_display else ""
                logger.info("Announced startup to %s%s", peer, version_suffix)
                break
        else:
            logger.warning("Unable to notify node %s of startup", peer)

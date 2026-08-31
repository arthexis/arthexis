"""Cryptographic helpers for node key management and payload signing."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone as datetime_timezone
import logging
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings
from django.utils import timezone

if TYPE_CHECKING:
    from apps.nodes.models.node import Node

logger = logging.getLogger(__name__)


def ensure_keys(node: "Node") -> None:
    """Ensure ``node`` has an on-disk RSA keypair and synchronized public key."""
    security_dir = node.get_base_path() / "security"
    security_dir.mkdir(parents=True, exist_ok=True)
    priv_path = security_dir / f"{node.public_endpoint}"
    pub_path = security_dir / f"{node.public_endpoint}.pub"
    regenerate = not priv_path.exists() or not pub_path.exists()
    if not regenerate:
        key_max_age = getattr(settings, "NODE_KEY_MAX_AGE", timedelta(days=90))
        if key_max_age is not None:
            try:
                priv_mtime = datetime.fromtimestamp(priv_path.stat().st_mtime, tz=datetime_timezone.utc)
                pub_mtime = datetime.fromtimestamp(pub_path.stat().st_mtime, tz=datetime_timezone.utc)
            except OSError:
                regenerate = True
            else:
                cutoff = timezone.now() - key_max_age
                if priv_mtime < cutoff or pub_mtime < cutoff:
                    regenerate = True
    if regenerate:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        priv_path.write_bytes(private_bytes)
        pub_path.write_bytes(public_bytes)
        try:
            priv_path.chmod(0o600)
            pub_path.chmod(0o644)
        except OSError:
            logger.warning("Unable to set key permissions for %s", node)
        public_text = public_bytes.decode()
        if node.public_key != public_text:
            node.public_key = public_text
            node.save(update_fields=["public_key"])
    elif not node.public_key:
        node.public_key = pub_path.read_text()
        node.save(update_fields=["public_key"])


def get_private_key(node: "Node"):
    """Return the loaded private key object for ``node`` when available."""
    if not node.public_endpoint:
        return None
    try:
        ensure_keys(node)
    except Exception:
        return None
    priv_path = node.get_base_path() / "security" / f"{node.public_endpoint}"
    try:
        return serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
    except Exception:
        return None


def sign_payload(payload: str, private_key) -> tuple[str | None, str | None]:
    """Sign ``payload`` and return a ``(signature, error)`` tuple."""
    if not private_key:
        return None, "Private key unavailable"
    try:
        signature = private_key.sign(
            payload.encode(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to sign payload: %s", exc)
        return None, str(exc)
    return base64.b64encode(signature).decode("ascii"), None

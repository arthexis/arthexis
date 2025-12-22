from __future__ import annotations

import asyncio
import contextlib
import base64
import ipaddress
import json
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import quote, urlsplit, urlunsplit

import requests
import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from requests import RequestException

from apps.nodes.models import Node
from apps.ocpp.network import serialize_charger_for_network


def load_local_node_credentials():
    """Return the local node and private key required for signed requests."""

    local_node = Node.get_local()
    if not local_node:
        return None, None, _("Local node is not registered.")

    endpoint = (local_node.public_endpoint or "").strip()
    if not endpoint:
        return local_node, None, _("Local node public endpoint is not configured.")

    security_dir = local_node.get_base_path() / "security"
    priv_path = security_dir / endpoint
    if not priv_path.exists():
        return local_node, None, _("Local node private key not found.")

    try:
        private_key = serialization.load_pem_private_key(
            priv_path.read_bytes(), password=None
        )
    except Exception as exc:  # pragma: no cover - unexpected key errors
        return (
            local_node,
            None,
            _("Failed to load private key: %(error)s") % {"error": exc},
        )

    return local_node, private_key, None


def sign_payload(private_key, payload: str) -> str:
    """Return a base64 encoded signature for the provided payload."""

    return base64.b64encode(
        private_key.sign(
            payload.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
    ).decode()


def _iter_forwarding_urls(node: Node, charger_id: str) -> Iterable[str]:
    """Yield websocket URLs that may accept the provided charge point."""

    safe_id = quote(str(charger_id))
    for base in node.iter_remote_urls("/"):
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"}:
            continue
        hostname = parsed.hostname or ""
        if parsed.scheme == "https" and hostname:
            try:
                ipaddress.ip_address(hostname)
            except ValueError:
                pass
            else:
                continue
        scheme = "wss" if parsed.scheme == "https" else "ws"
        base_path = parsed.path.rstrip("/")
        for prefix in ("", "/ws"):
            path = f"{base_path}{prefix}/{safe_id}".replace("//", "/")
            if not path.startswith("/"):
                path = f"/{path}"
            yield urlunsplit((scheme, parsed.netloc, path, "", ""))


async def _probe_websocket(url: str) -> bool:
    try:
        async with websockets.connect(url, open_timeout=3, close_timeout=1):
            return True
    except Exception:
        return False


def attempt_forwarding_probe(node: Node, charger_id: str) -> bool:
    """Try to connect to the target node for the provided charger."""

    if not charger_id:
        return False

    for url in _iter_forwarding_urls(node, charger_id):
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(_probe_websocket(url))
        except Exception:
            result = False
        finally:
            loop.close()
        if result:
            return True
    return False


def send_forwarding_metadata(
    target: Node,
    chargers: Iterable,
    local_node: Node,
    private_key,
    *,
    forwarded_messages: Iterable[str] | None = None,
) -> tuple[bool, str | None]:
    """Send metadata for forwarded chargers to the target node."""

    chargers = list(chargers)
    if not chargers:
        return True, None

    forwarded_list: list[str] | None
    if forwarded_messages is None:
        forwarded_list = None
    else:
        forwarded_list = [str(value) for value in forwarded_messages if value]

    payload = {
        "requester": str(local_node.uuid),
        "requester_mac": local_node.mac_address,
        "requester_public_key": local_node.public_key,
        "chargers": [
            serialize_charger_for_network(
                charger,
                forwarded_messages=forwarded_list,
            )
            for charger in chargers
        ],
        "transactions": {"chargers": [], "transactions": []},
    }
    payload_json = json.dumps(
        payload, separators=(",", ":"), sort_keys=True, default=str
    )
    headers = {"Content-Type": "application/json"}
    if private_key:
        headers["X-Signature"] = sign_payload(private_key, payload_json)

    errors: list[str] = []
    for url in target.iter_remote_urls("/nodes/network/chargers/forward/"):
        if not url:
            continue

        response = None
        try:
            response = requests.post(url, data=payload_json, headers=headers, timeout=5)
        except RequestException as exc:
            errors.append(
                _(
                    "Failed to send forwarding metadata to %(node)s via %(url)s (%(error)s)."
                )
                % {"node": target, "url": url, "error": exc}
            )
            continue

        try:
            try:
                data: Mapping = response.json()
            except ValueError:
                data = {}

            if response.ok and isinstance(data, Mapping) and data.get("status") == "ok":
                return True, None

            detail = ""
            if isinstance(data, Mapping):
                detail = data.get("detail") or ""
            errors.append(
                _(
                    "Forwarding metadata to %(node)s via %(url)s failed: %(status)s %(detail)s"
                )
                % {
                    "node": target,
                    "url": url,
                    "status": response.status_code,
                    "detail": detail,
                }
            )
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()

    if not errors:
        return False, _("No reachable host found for %(node)s.") % {"node": target}
    return False, errors[-1].strip()

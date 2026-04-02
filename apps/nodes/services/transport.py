"""Node-to-node transport abstractions for registration and NetMessage delivery."""

from __future__ import annotations

import json
import logging
import socket
from pathlib import Path

from django.conf import settings

from apps.nodes.models.node import Node

logger = logging.getLogger(__name__)


class TransportError(Exception):
    """Raised when a transport backend cannot deliver a payload."""


def _is_ipc_enabled() -> bool:
    return bool(getattr(settings, "NODES_ENABLE_SIBLING_IPC", False))


def _is_secure_socket_path(path: Path) -> bool:
    try:
        stat_result = path.stat()
    except OSError:
        return False
    # Require owner-only socket access to keep sibling IPC local and private.
    return (stat_result.st_mode & 0o077) == 0


def _request_via_unix_socket(*, socket_path: Path, operation: str, payload: dict[str, object]) -> dict[str, object]:
    if not _is_ipc_enabled():
        raise TransportError("sibling ipc disabled")
    if not socket_path.exists():
        raise TransportError("ipc socket unavailable")
    if not _is_secure_socket_path(socket_path):
        raise PermissionError("ipc socket permissions are too broad")

    request_payload = {
        "operation": operation,
        "payload": payload,
    }
    raw = json.dumps(request_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(1.0)
    try:
        client.connect(str(socket_path))
        client.sendall(raw + b"\n")
        response = b""
        while not response.endswith(b"\n"):
            chunk = client.recv(8192)
            if not chunk:
                break
            response += chunk
    except OSError as exc:
        raise TransportError(str(exc)) from exc
    finally:
        client.close()

    if not response:
        raise TransportError("empty ipc response")
    try:
        parsed = json.loads(response.decode("utf-8").strip() or "{}")
    except json.JSONDecodeError as exc:
        raise TransportError("invalid ipc response") from exc
    if not isinstance(parsed, dict):
        raise TransportError("invalid ipc response shape")
    return parsed


def _post_json(url: str, *, payload_json: str, headers: dict[str, str], timeout: float = 1.0) -> bool:
    """Send a JSON payload over HTTP and return ``True`` on success."""

    import requests

    try:
        response = requests.post(url, data=payload_json, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        logger.debug("Transport HTTP POST failed for %s: %s", url, exc)
        return False
    return bool(response.ok)


def send_registration(payload: dict[str, object], target_node: Node) -> bool:
    """Send a registration payload using preferred transport for ``target_node``."""

    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    headers = {"Content-Type": "application/json"}

    if target_node.current_relation == Node.Relation.SIBLING:
        socket_path = target_node.get_ipc_socket_path()
        if socket_path:
            try:
                response = _request_via_unix_socket(
                    socket_path=socket_path,
                    operation="registration",
                    payload=payload,
                )
            except (PermissionError, TransportError) as exc:
                logger.info("Sibling registration IPC failed for node %s: %s", target_node.pk, exc)
            else:
                if bool(response.get("ok")):
                    return True
                logger.info(
                    "Sibling registration IPC rejected for node %s: %s; falling back to HTTP",
                    target_node.pk,
                    response.get("detail", "unknown error"),
                )

    for url in target_node.iter_remote_urls("/nodes/register/"):
        if _post_json(url, payload_json=payload_json, headers=headers, timeout=2.0):
            return True
    return False


def send_net_message(
    payload: dict[str, object],
    target_node: Node,
    *,
    payload_json: str,
    headers: dict[str, str],
) -> bool:
    """Send a NetMessage payload to ``target_node`` over preferred transport."""

    if target_node.current_relation == Node.Relation.SIBLING:
        socket_path = target_node.get_ipc_socket_path()
        if socket_path:
            ipc_payload = {
                "payload": payload,
                "signature": headers.get("X-Signature", ""),
            }
            try:
                response = _request_via_unix_socket(
                    socket_path=socket_path,
                    operation="net_message",
                    payload=ipc_payload,
                )
            except (PermissionError, TransportError) as exc:
                logger.info("Sibling net message IPC failed for node %s: %s", target_node.pk, exc)
            else:
                if bool(response.get("ok")):
                    return True
                logger.info(
                    "Sibling net message IPC rejected for node %s: %s; falling back to HTTP",
                    target_node.pk,
                    response.get("detail", "unknown error"),
                )

    for url in target_node.iter_remote_urls("/nodes/net-message/"):
        if _post_json(url, payload_json=payload_json, headers=headers):
            return True
    return False

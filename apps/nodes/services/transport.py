"""Node-to-node transport abstractions for registration and NetMessage delivery."""

from __future__ import annotations

import json
import logging
import socket
import stat
from pathlib import Path

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.utils._os import safe_join

from apps.nodes.models.node import Node
from apps.nodes.services.http_safety import (
    clean_http_header_value,
    quote_http_request_path,
)
from apps.nodes.services.path_safety import resolve_node_ipc_socket_path

logger = logging.getLogger(__name__)


class TransportError(Exception):
    """Raised when a transport backend cannot deliver a payload."""


def _is_ipc_enabled() -> bool:
    return bool(getattr(settings, "NODES_ENABLE_SIBLING_IPC", False))


def _resolve_managed_socket_path(
    socket_path: Path | None, managed_root: Path
) -> Path | None:
    if not socket_path:
        return None
    try:
        resolved_root = managed_root.resolve(strict=False)
        candidate_path = Path(socket_path)
        if candidate_path.is_absolute():
            relative_socket = None
            for base_path in (resolved_root, managed_root):
                try:
                    relative_socket = candidate_path.relative_to(base_path)
                    break
                except ValueError:
                    continue
            if relative_socket is None:
                return None
        else:
            try:
                relative_socket = candidate_path.relative_to(managed_root)
            except ValueError:
                relative_socket = candidate_path
        safe_path = Path(safe_join(resolved_root, str(relative_socket)))
        resolved_socket = safe_path.resolve(strict=False)
        if not resolved_socket.is_relative_to(resolved_root):
            return None
        return safe_path
    except (OSError, RuntimeError, SuspiciousFileOperation, ValueError):
        return None


def _is_secure_socket_path(path: Path) -> bool:
    checked_path = _resolve_managed_socket_path(path, path.parent)
    if checked_path is None:
        return False
    try:
        stat_result = checked_path.stat()
    except OSError:
        return False
    # Require owner-only socket access to keep sibling IPC local and private.
    return (stat_result.st_mode & 0o077) == 0


def _get_unix_socket_path_status(
    socket_path: Path | None, *, managed_root: Path
) -> dict[str, object]:
    """Return readiness details for a Unix socket path."""

    checked_path = _resolve_managed_socket_path(socket_path, managed_root)
    result: dict[str, object] = {
        "transport": "unix_socket",
        "configured": bool(socket_path),
        "ready": False,
        "path": str(socket_path or ""),
    }
    if not socket_path:
        result["status"] = "unconfigured"
        return result
    if checked_path is None:
        result["status"] = "rejected_path"
        return result
    result["path"] = str(checked_path)
    if not hasattr(socket, "AF_UNIX"):
        result["status"] = "unsupported"
        return result
    if not checked_path.exists():
        result["status"] = "missing"
        return result
    try:
        stat_result = checked_path.stat()
    except OSError as exc:
        result.update({"status": "error", "detail": str(exc)})
        return result

    socket_mode = stat_result.st_mode & 0o777
    result["mode"] = f"{socket_mode:o}"
    if not stat.S_ISSOCK(stat_result.st_mode):
        result["status"] = "wrong_type"
        return result
    # Require owner-only socket access to keep sibling IPC local and private.
    if stat_result.st_mode & 0o077:
        result["status"] = "rejected_permissions"
        return result
    result.update({"status": "ready", "ready": True, "secure": True})
    return result


def get_unix_socket_status(target_node: Node) -> dict[str, object]:
    """Return readiness details for a node Unix socket transport."""

    configured_path = target_node.get_ipc_socket_path()
    socket_path = resolve_node_ipc_socket_path(target_node)
    managed_root = target_node.get_base_path() / "ipc"
    if configured_path and socket_path is None:
        return {
            "transport": "unix_socket",
            "configured": True,
            "ready": False,
            "path": str(configured_path),
            "status": "rejected_path",
        }
    return _get_unix_socket_path_status(socket_path, managed_root=managed_root)


def post_json_via_unix_socket(
    *,
    socket_path: Path,
    path: str,
    payload_json: str,
    headers: dict[str, str],
    managed_root: Path,
    timeout: float = 5.0,
    host: str = "localhost",
) -> dict[str, object]:
    """Send an HTTP JSON POST through a local Unix socket."""

    status = _get_unix_socket_path_status(socket_path, managed_root=managed_root)
    if status.get("status") != "ready":
        raise TransportError(f"local unix socket not ready: {status.get('status')}")
    checked_socket_path = Path(str(status["path"]))

    normalized_path = quote_http_request_path(path)
    body = payload_json.encode("utf-8")
    host_header = clean_http_header_value(host, default="localhost")
    header_lines = [
        f"POST {normalized_path} HTTP/1.1",
        f"Host: {host_header}",
        "Connection: close",
        f"Content-Length: {len(body)}",
    ]
    for key, value in headers.items():
        key_text = str(key).strip()
        value_text = str(value)
        if not key_text or any(char in key_text for char in "\r\n:"):
            continue
        if any(char in value_text for char in "\r\n"):
            continue
        if key_text.lower() in {"host", "connection", "content-length"}:
            continue
        header_lines.append(f"{key_text}: {value_text}")
    raw_request = ("\r\n".join(header_lines) + "\r\n\r\n").encode("utf-8") + body

    if not hasattr(socket, "AF_UNIX"):
        raise TransportError("Unix sockets are not supported on this platform")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(checked_socket_path))
        client.sendall(raw_request)
        response_chunks: list[bytes] = []
        while True:
            chunk = client.recv(8192)
            if not chunk:
                break
            response_chunks.append(chunk)
    except OSError as exc:
        raise TransportError(str(exc)) from exc
    finally:
        client.close()

    if not response_chunks:
        raise TransportError("empty unix socket HTTP response")
    response = b"".join(response_chunks)
    header_blob, _, body_blob = response.partition(b"\r\n\r\n")
    header_lines = header_blob.splitlines()
    if not header_lines:
        raise TransportError("invalid unix socket HTTP response")
    status_line = header_lines[0].decode("iso-8859-1", errors="replace")
    parts = status_line.split(" ", 2)
    try:
        status_code = int(parts[1])
    except (IndexError, ValueError) as exc:
        raise TransportError("invalid unix socket HTTP response") from exc
    body_text = body_blob.decode("utf-8", errors="replace")
    return {
        "ok": 200 <= status_code < 300,
        "status_code": status_code,
        "body": body_text,
    }


def _request_via_unix_socket(
    *,
    socket_path: Path,
    managed_root: Path,
    operation: str,
    payload: dict[str, object],
) -> dict[str, object]:
    if not _is_ipc_enabled():
        raise TransportError("sibling ipc disabled")
    checked_path = _resolve_managed_socket_path(socket_path, managed_root)
    if checked_path is None:
        raise TransportError("ipc socket path rejected")
    socket_path = checked_path
    if not socket_path.exists():
        raise TransportError("ipc socket unavailable")
    try:
        stat_result = socket_path.stat()
    except OSError as exc:
        raise TransportError(str(exc)) from exc
    if not stat.S_ISSOCK(stat_result.st_mode):
        raise TransportError("ipc socket is not a socket")
    if stat_result.st_mode & 0o077:
        raise PermissionError("ipc socket permissions are too broad")

    request_payload = {
        "operation": operation,
        "payload": payload,
    }
    raw = json.dumps(request_payload, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )

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


def _post_json(
    url: str, *, payload_json: str, headers: dict[str, str], timeout: float = 1.0
) -> bool:
    """Send a JSON payload over HTTP and return ``True`` on success."""

    import requests

    try:
        response = requests.post(
            url, data=payload_json, headers=headers, timeout=timeout
        )
    except requests.RequestException as exc:
        logger.debug("Transport HTTP POST failed for %s: %s", url, exc)
        return False
    return bool(response.ok)


def send_registration(payload: dict[str, object], target_node: Node) -> bool:
    """Send a registration payload using preferred transport for ``target_node``."""

    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    headers = {"Content-Type": "application/json"}

    if target_node.current_relation == Node.Relation.SIBLING:
        socket_path = resolve_node_ipc_socket_path(target_node)
        if socket_path:
            try:
                response = _request_via_unix_socket(
                    socket_path=socket_path,
                    managed_root=target_node.get_base_path() / "ipc",
                    operation="registration",
                    payload=payload,
                )
            except (PermissionError, TransportError) as exc:
                logger.info(
                    "Sibling registration IPC failed for node %s: %s",
                    target_node.pk,
                    exc,
                )
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
        socket_path = resolve_node_ipc_socket_path(target_node)
        if socket_path:
            ipc_payload = {
                "payload": payload,
                "signature": headers.get("X-Signature", ""),
            }
            try:
                response = _request_via_unix_socket(
                    socket_path=socket_path,
                    managed_root=target_node.get_base_path() / "ipc",
                    operation="net_message",
                    payload=ipc_payload,
                )
            except (PermissionError, TransportError) as exc:
                logger.info(
                    "Sibling net message IPC failed for node %s: %s",
                    target_node.pk,
                    exc,
                )
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

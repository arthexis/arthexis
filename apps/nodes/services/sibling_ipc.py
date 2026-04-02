"""Local UNIX socket IPC endpoint for sibling node operations."""

from __future__ import annotations

import json
import logging
import os
import socketserver
import threading
from base64 import b64decode
from binascii import Error as BinasciiError
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings
from django.http import HttpRequest
from django.test.client import RequestFactory

from apps.nodes.models.net_message import NetMessage
from apps.nodes.models.node import Node
from apps.nodes.views.registration.handlers import register_node

logger = logging.getLogger(__name__)

_SERVER_THREAD: threading.Thread | None = None
_SERVER: socketserver.UnixStreamServer | None = None


def _is_enabled() -> bool:
    return bool(getattr(settings, "NODES_ENABLE_SIBLING_IPC", False))


def _relation_is_sibling(node: Node | None) -> bool:
    return bool(node and node.current_relation == Node.Relation.SIBLING)


def _build_register_request(payload: dict[str, object]) -> HttpRequest:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return RequestFactory().post(
        "/nodes/register/",
        data=body,
        content_type="application/json",
    )


def _signature_is_valid(sender: Node, signature: str, msg_payload: dict[str, object]) -> bool:
    """Verify a sibling IPC net message signature against the sender key."""

    if not sender.public_key:
        return False
    try:
        signature_bytes = b64decode(signature)
        public_key = serialization.load_pem_public_key(sender.public_key.encode())
        signed_payload = json.dumps(
            msg_payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        public_key.verify(
            signature_bytes,
            signed_payload,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
    except (BinasciiError, InvalidSignature, TypeError, ValueError):
        return False
    return True


def handle_operation(operation: str, payload: dict[str, object]) -> dict[str, object]:
    """Handle an inbound sibling IPC operation and return status details."""

    if operation == "registration":
        sender = None
        mac_address = str(payload.get("mac_address") or "").strip()
        if mac_address:
            sender = Node.objects.filter(mac_address__iexact=mac_address).first()
        if not sender and payload.get("public_key"):
            sender = Node.objects.filter(public_key=str(payload.get("public_key"))).first()
        if not _relation_is_sibling(sender):
            return {"ok": False, "detail": "sibling relation required"}
        response = register_node(_build_register_request(payload))
        return {"ok": response.status_code == 200, "status": response.status_code}

    if operation == "net_message":
        msg_payload = payload.get("payload")
        signature = str(payload.get("signature") or "")
        if not isinstance(msg_payload, dict):
            return {"ok": False, "detail": "payload required"}
        sender_id = msg_payload.get("sender")
        sender = Node.objects.filter(uuid=sender_id).first() if sender_id else None
        if not _relation_is_sibling(sender):
            return {"ok": False, "detail": "sibling relation required"}
        if not signature:
            return {"ok": False, "detail": "signature required"}
        if not _signature_is_valid(sender, signature, msg_payload):
            return {"ok": False, "detail": "invalid signature"}
        try:
            NetMessage.receive_payload(msg_payload, sender=sender)
        except ValueError as exc:
            return {"ok": False, "detail": str(exc)}
        return {"ok": True}

    return {"ok": False, "detail": "unknown operation"}


class _SiblingIPCHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(1024 * 1024)
        response = {"ok": False, "detail": "invalid request"}
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            operation = str(data.get("operation") or "").strip()
            payload = data.get("payload")
            if operation and isinstance(payload, dict):
                response = handle_operation(operation, payload)
        self.wfile.write((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))


class _SiblingIPCServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def _prepare_socket_path(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if path.exists():
        path.unlink()


def start_server() -> Path | None:
    """Start sibling IPC socket server when enabled and local node exists."""

    global _SERVER, _SERVER_THREAD

    if _SERVER_THREAD and _SERVER_THREAD.is_alive():
        return None
    if not _is_enabled():
        return None

    local = Node.get_local()
    if not local:
        return None
    path = local.get_ipc_socket_path()
    if not path:
        return None

    try:
        _prepare_socket_path(path)
        server = _SiblingIPCServer(str(path), _SiblingIPCHandler)
        os.chmod(path, 0o600)
    except OSError as exc:
        logger.warning("Unable to start sibling IPC server at %s: %s", path, exc)
        return None

    thread = threading.Thread(target=server.serve_forever, name="nodes-sibling-ipc", daemon=True)
    thread.start()
    _SERVER = server
    _SERVER_THREAD = thread
    logger.info("Sibling IPC server started at %s", path)
    return path

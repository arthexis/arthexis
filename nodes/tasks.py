import base64
import json
import logging
from pathlib import Path

import pyperclip
import requests
from celery import shared_task
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from pyperclip import PyperclipException

from .models import ContentSample, NetMessage, Node
from .utils import capture_screenshot, save_screenshot

logger = logging.getLogger(__name__)


@shared_task
def sample_clipboard() -> None:
    """Save current clipboard contents to a :class:`ContentSample` entry."""
    try:
        content = pyperclip.paste()
    except PyperclipException as exc:  # pragma: no cover - depends on OS clipboard
        logger.error("Clipboard error: %s", exc)
        return
    if not content:
        logger.info("Clipboard is empty")
        return
    if ContentSample.objects.filter(content=content, kind=ContentSample.TEXT).exists():
        logger.info("Duplicate clipboard content; sample not created")
        return
    node = Node.get_local()
    ContentSample.objects.create(content=content, node=node, kind=ContentSample.TEXT)


@shared_task
def capture_node_screenshot(
    url: str | None = None, port: int = 8000, method: str = "TASK"
) -> str:
    """Capture a screenshot of ``url`` and record it as a :class:`ContentSample`."""
    if url is None:
        url = f"http://localhost:{port}"
    try:
        path: Path = capture_screenshot(url)
    except Exception as exc:  # pragma: no cover - depends on selenium setup
        logger.error("Screenshot capture failed: %s", exc)
        return ""
    node = Node.get_local()
    save_screenshot(path, node=node, method=method)
    return str(path)


@shared_task
def poll_unreachable_upstream() -> None:
    """Poll upstream nodes for queued NetMessages."""

    local = Node.get_local()
    if not local or not local.has_feature("celery-queue"):
        return

    private_key = local.get_private_key()
    if not private_key:
        logger.warning("Node %s cannot sign upstream polls", getattr(local, "pk", None))
        return

    requester_payload = {"requester": str(local.uuid)}
    payload_json = json.dumps(requester_payload, separators=(",", ":"), sort_keys=True)
    try:
        signature = base64.b64encode(
            private_key.sign(
                payload_json.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        ).decode()
    except Exception as exc:
        logger.warning("Failed to sign upstream poll request: %s", exc)
        return

    headers = {"Content-Type": "application/json", "X-Signature": signature}
    upstream_nodes = Node.objects.filter(current_relation=Node.Relation.UPSTREAM)
    for upstream in upstream_nodes:
        if not upstream.public_key:
            continue
        host = (upstream.address or upstream.hostname or "").strip()
        if not host:
            continue
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = upstream.port or 8000
        if port in {80, 443}:
            url = f"http://{host}/nodes/net-message/pull/"
        else:
            url = f"http://{host}:{port}/nodes/net-message/pull/"
        try:
            response = requests.post(url, data=payload_json, headers=headers, timeout=5)
        except Exception as exc:
            logger.warning("Polling upstream node %s failed: %s", upstream.pk, exc)
            continue
        if not response.ok:
            logger.warning(
                "Upstream node %s returned status %s", upstream.pk, response.status_code
            )
            continue
        try:
            body = response.json()
        except ValueError:
            logger.warning("Upstream node %s returned invalid JSON", upstream.pk)
            continue
        messages = body.get("messages", [])
        if not isinstance(messages, list) or not messages:
            continue
        try:
            public_key = serialization.load_pem_public_key(upstream.public_key.encode())
        except Exception:
            logger.warning("Upstream node %s has invalid public key", upstream.pk)
            continue
        for item in messages:
            if not isinstance(item, dict):
                continue
            payload = item.get("payload")
            payload_signature = item.get("signature")
            if not isinstance(payload, dict) or not payload_signature:
                continue
            payload_text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
            try:
                public_key.verify(
                    base64.b64decode(payload_signature),
                    payload_text.encode(),
                    padding.PKCS1v15(),
                    hashes.SHA256(),
                )
            except Exception:
                logger.warning(
                    "Signature verification failed for upstream node %s", upstream.pk
                )
                continue
            try:
                NetMessage.receive_payload(payload, sender=upstream)
            except ValueError as exc:
                logger.warning(
                    "Discarded upstream message from node %s: %s", upstream.pk, exc
                )

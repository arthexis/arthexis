import base64
import ipaddress
import json
import logging
import socket
import time
from collections.abc import Callable
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pyperclip
import requests
from celery import shared_task
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from pyperclip import PyperclipException

from django.conf import settings
from django.contrib import admin
from django.utils import timezone as django_timezone

from .models import ContentSample, NetMessage, Node, PendingNetMessage
from .utils import capture_screenshot, save_screenshot

logger = logging.getLogger(__name__)


CONSTELLATION_PROBE_PAYLOAD = b"constellation-udp-probe"

def _iter_constellation_probe_addresses(node: Node) -> list[str]:
    """Return unique IP addresses that may reach ``node``."""

    addresses: list[str] = []
    direct_ip = (getattr(node, "constellation_ip", "") or "").strip()
    if direct_ip:
        try:
            ipaddress.ip_address(direct_ip)
        except ValueError:
            pass
        else:
            addresses.append(direct_ip)
    for candidate in node.get_remote_host_candidates():
        candidate = (candidate or "").strip()
        if not candidate:
            continue
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if candidate not in addresses:
            addresses.append(candidate)
    return addresses


def _send_udp_probe(address: str, port: int) -> bool:
    """Send a UDP probe packet to ``address`` and return ``True`` on success."""

    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False

    family = socket.AF_INET6 if parsed.version == 6 else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.setblocking(False)
            sock.sendto(CONSTELLATION_PROBE_PAYLOAD, (address, port))
    except OSError as exc:  # pragma: no cover - network stack dependent
        logger.debug("Constellation UDP probe to %s:%s failed: %s", address, port, exc)
        return False
    return True


def _synchronize_constellation_udp_window(
    interval_seconds: int,
    *,
    now_func: Callable[[], float] = time.time,
    sleep_func: Callable[[float], None] = time.sleep,
) -> float:
    """Wait until the next shared probe window and return the epoch timestamp."""

    try:
        interval = int(interval_seconds)
    except (TypeError, ValueError):
        interval = 30
    if interval <= 0:
        return now_func()

    start = now_func()
    remainder = start % interval
    if remainder:
        wait_seconds = interval - remainder
        if wait_seconds > 0:
            sleep_func(wait_seconds)
            start = now_func()

    return start


@shared_task
def kickstart_constellation_udp() -> int:
    """Probe constellation peers to encourage WireGuard synchronization."""

    local = Node.get_local()
    if not local or not (local.constellation_ip or "").strip():
        logger.debug(
            "Skipping constellation UDP kickstart: local node lacks an overlay address"
        )
        return 0

    try:
        port = int(getattr(settings, "CONSTELLATION_WG_PORT", 51820))
    except (TypeError, ValueError):
        port = 51820

    local_pk = getattr(local, "pk", None)
    local_mac = (local.mac_address or "").strip().lower()
    local_ip = (local.constellation_ip or "").strip()

    interval = getattr(
        settings, "CONSTELLATION_UDP_PROBE_INTERVAL_SECONDS", 30
    )
    window_epoch = _synchronize_constellation_udp_window(interval)
    window_label = datetime.fromtimestamp(window_epoch, tz=timezone.utc).isoformat()
    logger.debug("Constellation UDP probe window reached at %s", window_label)

    queryset = Node.objects.filter(constellation_ip__isnull=False).order_by("pk")
    if local_pk is not None:
        queryset = queryset.exclude(pk=local_pk)

    probes_sent = 0
    seen: set[tuple[str, int]] = set()

    for peer in queryset.iterator():
        peer_mac = (peer.mac_address or "").strip().lower()
        if local_mac and peer_mac and peer_mac == local_mac:
            continue
        if not (peer.constellation_ip or "").strip():
            continue

        for address in _iter_constellation_probe_addresses(peer):
            if local_ip and address == local_ip:
                continue
            target = (address, port)
            if target in seen:
                continue
            if _send_udp_probe(address, port):
                probes_sent += 1
            seen.add(target)

    return probes_sent


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
    url: str | None = None, port: int = 8888, method: str = "TASK"
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
        response = None
        for url in upstream.iter_remote_urls("/nodes/net-message/pull/"):
            try:
                response = requests.post(
                    url, data=payload_json, headers=headers, timeout=5
                )
            except Exception as exc:
                logger.warning("Polling upstream node %s via %s failed: %s", upstream.pk, url, exc)
                continue
            if response.ok:
                break
            logger.warning(
                "Upstream node %s returned status %s", upstream.pk, response.status_code
            )
            response = None
        if response is None or not response.ok:
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


def _resolve_node_admin():
    """Return the registered :class:`~django.contrib.admin.ModelAdmin` for nodes."""

    node_admin = admin.site._registry.get(Node)
    if node_admin is not None:
        return node_admin

    from .admin import NodeAdmin  # Avoid importing at module load time

    return NodeAdmin(Node, admin.site)


def _summarize_update_results(local_result: dict | None, remote_result: dict | None) -> str:
    """Return ``success``, ``partial`` or ``error`` based on admin responses."""

    local_ok = bool(local_result.get("ok")) if isinstance(local_result, dict) else False
    remote_ok = bool(remote_result.get("ok")) if isinstance(remote_result, dict) else False
    if local_ok and remote_ok:
        return "success"
    if local_ok or remote_ok:
        return "partial"
    return "error"


@shared_task
def update_all_nodes_information() -> dict:
    """Invoke the admin "Update nodes" workflow for every node."""

    summary = {
        "total": 0,
        "success": 0,
        "partial": 0,
        "error": 0,
        "results": [],
    }

    local_node = Node.get_local()
    if local_node is None or not local_node.has_feature("celery-queue"):
        logger.info(
            "Skipping daily node refresh; local node missing celery-queue feature"
        )
        summary["skipped"] = True
        summary["reason"] = "Local node missing celery-queue feature"
        return summary

    node_admin = _resolve_node_admin()

    for node in Node.objects.order_by("pk").iterator():
        summary["total"] += 1
        try:
            local_result = node_admin._refresh_local_information(node)
        except Exception as exc:  # pragma: no cover - unexpected admin failure
            logger.exception("Local refresh failed for node %s", node.pk)
            local_result = {"ok": False, "message": str(exc)}

        try:
            remote_result = node_admin._push_remote_information(node)
        except Exception as exc:  # pragma: no cover - unexpected admin failure
            logger.exception("Remote update failed for node %s", node.pk)
            remote_result = {"ok": False, "message": str(exc)}

        status = _summarize_update_results(local_result, remote_result)
        summary[status] += 1
        summary["results"].append(
            {
                "node_id": node.pk,
                "node": str(node),
                "status": status,
                "local": local_result,
                "remote": remote_result,
            }
        )

    return summary


@shared_task
def purge_stale_net_messages(retention_hours: int = 24) -> int:
    """Remove NetMessages (and pending queue entries) older than ``retention_hours``."""

    try:
        hours = int(retention_hours)
    except (TypeError, ValueError):
        hours = 24
    if hours < 0:
        hours = 0

    cutoff = django_timezone.now() - timedelta(hours=hours)
    message_delete_result = NetMessage.objects.filter(created__lt=cutoff).delete()
    message_count = message_delete_result[1].get(NetMessage._meta.label, 0)

    pending_delete_result = PendingNetMessage.objects.filter(
        queued_at__lt=cutoff
    ).delete()
    pending_count = pending_delete_result[1].get(
        PendingNetMessage._meta.label,
        0,
    )

    return message_count + pending_count

import base64
import json
import logging
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import requests
from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.contrib import admin
from django.utils import timezone as django_timezone

from apps.content.models import ContentSample
from apps.screens.startup_notifications import (
    lcd_feature_enabled,
    queue_startup_message,
)
from .models import NetMessage, Node, PendingNetMessage
from .utils import capture_screenshot, save_screenshot

logger = logging.getLogger(__name__)

STARTUP_NET_MESSAGE_CACHE_KEY = "nodes:startup_net_message:sent"


@shared_task(name="nodes.tasks.sample_clipboard", bind=True)
def discard_sample_clipboard(self, *args, **kwargs) -> str:
    """Discard legacy clipboard polling tasks that may still be queued."""

    logger.warning(
        "Discarding legacy sample_clipboard task", extra={"args": args, "kwargs": kwargs}
    )
    return "discarded:sample_clipboard"


@shared_task
def send_startup_net_message(lock_file: str | None = None, port: str | None = None) -> str:
    """Queue the LCD startup Net Message once Celery is available."""

    try:
        # Prevent duplicate dispatches across multiple workers or reloads.
        if not cache.add(STARTUP_NET_MESSAGE_CACHE_KEY, True, timeout=None):
            return "skipped:already-sent"
    except Exception:
        logger.debug("Unable to set startup Net Message cache flag", exc_info=True)

    base_dir = Path(getattr(settings, "BASE_DIR", Path(__file__).resolve().parents[1]))
    target_lock = Path(lock_file) if lock_file else base_dir / ".locks" / "lcd_screen.lck"
    lock_dir = target_lock.parent.resolve()

    if not lcd_feature_enabled(lock_dir):
        return "skipped:lcd-disabled"

    port_value = port or os.environ.get("PORT", "8888")
    try:
        queue_startup_message(base_dir=base_dir, port=port_value, lock_file=target_lock)
    except Exception:
        logger.exception("Failed to queue startup Net Message")
        raise

    return f"queued:{target_lock}"


PING_FAILURE_CACHE_KEY = "nodes:connectivity:wlan1_failures"
PING_FAILURE_THRESHOLD = 3
PING_TARGET = "8.8.8.8"
PING_TIMEOUT_SECONDS = 5
PING_INTERFACE = "wlan1"


def _ping_target(target: str = PING_TARGET) -> tuple[bool, str]:
    """Return ``True`` when ``target`` is reachable via ICMP ping."""

    ping_path = shutil.which("ping")
    if not ping_path:
        return (False, "ping binary not available")

    try:
        result = subprocess.run(
            [
                ping_path,
                "-n",
                "-c",
                "1",
                "-W",
                str(PING_TIMEOUT_SECONDS),
                target,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=PING_TIMEOUT_SECONDS + 1,
        )
    except Exception as exc:  # pragma: no cover - unexpected execution failure
        return (False, f"ping failed: {exc}")

    if result.returncode == 0:
        return (True, result.stdout.strip())
    return (False, result.stderr.strip() or result.stdout.strip())


def _reset_wlan_interface(interface: str = PING_INTERFACE) -> dict[str, object]:
    """Attempt to reset ``interface`` using ``nmcli`` commands."""

    nmcli_path = shutil.which("nmcli")
    if not nmcli_path:
        return {"ok": False, "message": "nmcli not available"}

    commands = (
        [nmcli_path, "device", "disconnect", interface],
        [nmcli_path, "device", "connect", interface],
    )

    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=Node.NMCLI_TIMEOUT,
            )
        except Exception as exc:  # pragma: no cover - unexpected execution failure
            return {"ok": False, "message": f"command error: {exc}"}

        if result.returncode != 0:
            return {
                "ok": False,
                "message": result.stderr.strip() or result.stdout.strip(),
                "command": " ".join(command),
            }

    return {"ok": True, "message": f"Reset {interface} with nmcli"}


@shared_task
def capture_node_screenshot(
    url: str | None = None, port: int = 8888, method: str = "TASK"
) -> str:
    """Capture a screenshot of ``url`` and record it as a :class:`ContentSample`."""
    if url is None:
        node = Node.get_local()
        scheme = node.get_preferred_scheme() if node else "http"
        url = f"{scheme}://localhost:{port}"
    try:
        path: Path = capture_screenshot(url)
    except Exception as exc:  # pragma: no cover - depends on selenium setup
        logger.error("Screenshot capture failed: %s", exc)
        return ""
    node = Node.get_local()
    save_screenshot(path, node=node, method=method)
    return str(path)


@shared_task
def poll_upstream() -> None:
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
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
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
                    padding.PSS(
                        mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH,
                    ),
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
def poll_peers(enforce_feature: bool = True) -> dict:
    """Invoke the admin "Update nodes" workflow for peer nodes.

    When ``enforce_feature`` is False the celery-queue requirement is skipped to
    allow manual refreshes from management commands.
    """

    summary = {
        "total": 0,
        "success": 0,
        "partial": 0,
        "error": 0,
        "results": [],
    }

    try:
        local_node, _ = Node.register_current(notify_peers=False)
    except Exception as exc:  # pragma: no cover - unexpected registration failure
        logger.exception("Skipping hourly node refresh; failed to refresh local node")
        summary["skipped"] = True
        summary["reason"] = f"Local node registration failed: {exc}"
        return summary

    if local_node is None:
        logger.info("Skipping hourly node refresh; local node not registered")
        summary["skipped"] = True
        summary["reason"] = "Local node not registered"
        return summary

    if enforce_feature and not local_node.has_feature("celery-queue"):
        logger.info(
            "Skipping hourly node refresh; local node missing celery-queue feature"
        )
        summary["skipped"] = True
        summary["reason"] = "Local node missing celery-queue feature"
        return summary

    node_admin = _resolve_node_admin()

    peer_qs = Node.objects.filter(current_relation=Node.Relation.PEER)
    for node in peer_qs.order_by("pk").iterator():
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
def purge_net_messages(retention_hours: int = 24) -> int:
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


@shared_task
def monitor_nmcli() -> dict[str, object]:
    """Ping a known address and attempt to remediate repeated failures."""

    local = Node.get_local()
    if not local:
        return {"skipped": True, "reason": "Local node not registered"}

    if not local.has_feature("celery-queue"):
        return {"skipped": True, "reason": "Local node missing celery-queue feature"}

    role_name = getattr(getattr(local, "role", None), "name", None)
    if role_name not in Node.CONNECTIVITY_MONITOR_ROLES:
        return {"skipped": True, "reason": "Connectivity monitoring not enabled for role"}

    success, detail = _ping_target()
    if success:
        cache.set(PING_FAILURE_CACHE_KEY, 0, None)
        return {"ok": True, "detail": detail, "failures": 0}

    failures = cache.get(PING_FAILURE_CACHE_KEY, 0) + 1
    cache.set(PING_FAILURE_CACHE_KEY, failures, None)
    result: dict[str, object] = {"ok": False, "detail": detail, "failures": failures}

    if failures >= PING_FAILURE_THRESHOLD:
        remediation = _reset_wlan_interface()
        result["remediation"] = remediation
        if remediation.get("ok"):
            cache.set(PING_FAILURE_CACHE_KEY, 0, None)

    return result
